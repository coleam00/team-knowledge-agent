import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

import httpx

from app.db import MIGRATIONS, connect, migration_files
from app.git import git
from app.index_store import ChunkInput, replace_file
from app.models.fake import FakeEmbedder
from app.retrieval import Citation
from app.sources import Source
from tests.handbook import create_handbook_repo

ROOT = Path(__file__).resolve().parents[1]


class Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        path = attributes.get("src" if tag == "script" else "href")
        if tag in {"script", "link"} and path and path.startswith("/assets/"):
            self.paths.append(path)


@contextmanager
def running_app(
    environment: dict[str, str], observations: dict[str, object], label: str
) -> Iterator[tuple[subprocess.Popen[bytes], httpx.Client]]:
    with socket.socket() as allocation:
        allocation.bind(("127.0.0.1", 0))
        port = allocation.getsockname()[1]
    command = [sys.executable, "-m", "app", "serve", "--port", str(port)]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
        details: dict[str, object] = {"pid": process.pid, "argv": command}
        observations[label] = details
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
            ) as client:
                yield process, client
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            details["exit_code"] = process.returncode
            details["cleanup_confirmed"] = process.poll() is not None
            log.seek(0)
            details["log"] = log.read().decode("utf-8", errors="replace")


def wait_for_health(
    process: subprocess.Popen[bytes], client: httpx.Client, candidate: str, status: int
) -> httpx.Response:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Owned application exited before readiness")
        try:
            identity = client.get("/api/build-id")
        except httpx.TransportError:
            time.sleep(0.1)
            continue
        assert identity.status_code == 200
        assert identity.content == candidate.encode("utf-8"), "Wrong candidate identity"
        assert identity.headers["content-type"].startswith("text/plain")
        health = client.get("/api/health")
        if process.poll() is not None:
            raise RuntimeError("Owned application exited during readiness")
        if health.status_code == status:
            expected = "ok" if status == 200 else "unavailable"
            assert health.json() == {"status": expected, "db": expected}
            return health
        time.sleep(0.1)
    raise TimeoutError("Health did not reach the expected status within 45 seconds")


def inspect_page(client: httpx.Client) -> list[dict[str, object]]:
    response = client.get("/")
    assert response.status_code == 200
    assert "Team Knowledge Agent" in response.text
    assert response.headers["content-type"].startswith("text/html")
    parser = Assets()
    parser.feed(response.text)
    assert parser.paths, "Built page has no assets"
    observations: list[dict[str, object]] = []
    for path in parser.paths:
        asset = client.get(path)
        assert asset.status_code == 200 and asset.content
        content_type = asset.headers["content-type"]
        assert (
            "javascript" in content_type
            if path.endswith(".js")
            else content_type.startswith("text/css")
        )
        observations.append(
            {
                "path": path,
                "status": asset.status_code,
                "content_type": content_type,
                "bytes": len(asset.content),
            }
        )
    return observations


def absent_features(client: httpx.Client) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for method, path in [
        ("POST", "/api/sources/1/reindex"),
        ("POST", "/api/ask"),
        ("POST", "/mcp"),
    ]:
        response = client.request(method, path)
        assert response.status_code == 404
        observations.append(
            {"method": method, "path": path, "status": 404, "stage": "not yet built"}
        )
    return observations


def register_handbook(client: httpx.Client, state: Path) -> Source:
    repository = create_handbook_repo(state)
    response = client.post("/api/sources", json={"url": str(repository)})
    assert response.status_code == 202
    source = Source.model_validate(response.json())
    assert source.status == "queued"
    assert source.owner == "tka-fixtures" and source.name == "handbook"
    assert source.file_count == source.chunk_count == 0
    assert source.indexed_commit is None
    assert source.indexed_at is None
    assert source.error is None
    assert client.get(f"/api/sources/{source.id}").json() == response.json()
    duplicate = client.post("/api/sources", json={"url": str(repository)})
    assert duplicate.status_code == 409
    assert duplicate.json()["id"] == str(source.id)
    invalid = client.post(
        "/api/sources", json={"url": "https://gitlab.com/acme/handbook"}
    )
    assert invalid.status_code == 422
    return source


def ledger(database_url: str) -> list[dict[str, object]]:
    with connect(database_url) as connection:
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        ).fetchone() == ("vector",)
        rows = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in rows] == [
            version for version, _ in migration_files(MIGRATIONS)
        ]
        return [
            {"version": version, "applied_at": applied_at.isoformat()}
            for version, applied_at in rows
        ]


def seed_search(database_url: str, source: Source) -> Source:
    repository = Path(source.url)
    commit = git(repository, "rev-parse", "HEAD")
    model = FakeEmbedder()
    paths = ["docs/deploy.md", "docs/legacy-deploy.md", "docs/onboarding.md"]
    with connect(database_url) as connection, connection.transaction():
        for path in paths:
            content = (repository / path).read_bytes().decode("utf-8")
            replace_file(
                connection,
                source_id=source.id,
                path=path,
                blob_sha=git(repository, "rev-parse", f"{commit}:{path}"),
                language="markdown",
                chunks=[
                    ChunkInput(
                        start_line=1,
                        end_line=len(content.splitlines()),
                        content=content,
                        embedding=tuple(model.embed(content)),
                        embedding_model=model.model_id,
                    )
                ],
            )
        connection.execute(
            "UPDATE sources SET indexed_commit = %s, file_count = %s, chunk_count = %s "
            "WHERE id = %s",
            (commit, len(paths), len(paths), source.id),
        )
    return source.model_copy(
        update={
            "indexed_commit": commit,
            "file_count": len(paths),
            "chunk_count": len(paths),
        }
    )


def inspect_search(client: httpx.Client, source: Source) -> list[dict[str, object]]:
    parameters = {"q": "deploy", "source_id": str(source.id), "limit": "2"}
    response = client.get("/api/search", params=parameters)
    assert response.status_code == 200
    citations = [Citation.model_validate(item) for item in response.json()]
    assert len(citations) == 2
    assert [c.n for c in citations] == [1, 2]
    assert "docs/deploy.md" in {c.path for c in citations}
    for citation in citations:
        assert citation.source_id == source.id
        assert citation.commit == source.indexed_commit
        assert citation.repo == "tka-fixtures/handbook"
        content = (Path(source.url) / citation.path).read_bytes().decode("utf-8")
        assert citation.snippet == "".join(
            content.splitlines(keepends=True)[
                citation.start_line - 1 : citation.end_line
            ]
        )
        assert citation.url == (
            f"https://github.com/tka-fixtures/handbook/blob/{source.indexed_commit}/"
            f"{citation.path}#L{citation.start_line}-L{citation.end_line}"
        )
    assert client.get("/api/search", params=parameters).json() == response.json()
    for params in [
        {"q": "galaxies watermelon"},
        {"q": "deploy", "source_id": str(uuid4())},
    ]:
        empty = client.get("/api/search", params=params)
        assert empty.status_code == 200 and empty.json() == []
    for params in [{"q": ""}, {"q": "deploy", "limit": "0"}]:
        assert client.get("/api/search", params=params).status_code == 422
    return [citation.model_dump(mode="json") for citation in citations]


def verify(observations: dict[str, object]) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL must name a supplied fresh pgvector database")
    candidate = os.environ.get("FACTORY_RUNTIME_CANDIDATE")
    if candidate is None:
        raise ValueError("FACTORY_RUNTIME_CANDIDATE must identify this checkout")
    observations["candidate"] = candidate
    with connect(database_url) as connection:
        tables = connection.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        ).fetchall()
        assert not tables, (
            "Smoke requires a supplied fresh database with no user tables"
        )
    observations["fresh_database_user_tables"] = []
    with tempfile.TemporaryDirectory(prefix="tka-smoke-") as state:
        environment = os.environ.copy()
        environment.update(
            TKA_DATA_DIR=state, TKA_MODELS="fake", TKA_ALLOW_LOCAL_SOURCES="1"
        )
        environment.pop("OPENROUTER_API_KEY", None)
        observations["fresh_state"] = state
        with running_app(environment, observations, "first_start") as (process, client):
            health = wait_for_health(process, client, candidate, 200)
            observations["health"] = {
                "status": health.status_code,
                "body": health.json(),
            }
            observations["identity"] = {
                "status": 200,
                "body": client.get("/api/build-id").text,
            }
            observations["assets"] = inspect_page(client)
            assert client.get("/api/sources").json() == []
            source = register_handbook(client, Path(state))
            second = register_handbook(client, Path(state))
            assert source.id != second.id
            source = seed_search(database_url, source)
            second = seed_search(database_url, second)
            search_before = inspect_search(client, source)
            observations["seeded_search_before_restart"] = search_before
            observations["search_setup"] = "Persisted fixture rows; not ingestion"
            sources = [item.model_dump(mode="json") for item in (source, second)]
            assert client.get("/api/sources").json() == sources
            observations["sources_before_restart"] = sources
            observations["source_before_restart"] = source.model_dump(mode="json")
            observations["indexing"] = {
                "stage": "not yet built",
                "observed_status": source.status,
            }
            observations["unbuilt_features"] = absent_features(client)
            before = ledger(database_url)
            observations["ledger_before_restart"] = before
        with running_app(environment, observations, "restart") as (process, client):
            wait_for_health(process, client, candidate, 200)
            after = ledger(database_url)
            assert after == before
            observations["ledger_after_restart"] = after
            persisted = client.get(f"/api/sources/{source.id}")
            assert persisted.status_code == 200
            assert persisted.json() == source.model_dump(mode="json")
            persisted_sources = client.get("/api/sources").json()
            assert persisted_sources == sources
            observations["sources_after_restart"] = persisted_sources
            observations["source_after_restart"] = persisted.json()
            search_after = inspect_search(client, source)
            assert search_after == search_before
            observations["seeded_search_after_restart"] = search_after
        environment.pop("DATABASE_URL", None)
        with running_app(environment, observations, "missing_database") as (
            process,
            client,
        ):
            health = wait_for_health(process, client, candidate, 503)
            observations["missing_database_health"] = {
                "status": 503,
                "body": health.json(),
            }
            inspect_page(client)
    observations["state_cleanup_confirmed"] = not Path(state).exists()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    observations: dict[str, object] = {"cwd": str(ROOT)}
    try:
        verify(observations)
    except Exception as error:
        observations["passed"] = False
        observations["error_type"] = type(error).__name__
        print(
            "Smoke failed. Requires DATABASE_URL for a fresh pgvector database, "
            "FACTORY_RUNTIME_CANDIDATE, and a successful web build.",
            file=sys.stderr,
        )
        result = 1
    else:
        observations["passed"] = True
        result = 0
    output = json.dumps(observations, indent=2)
    print(output)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
