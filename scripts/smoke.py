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

import httpx

from app.config import Settings
from app.db import MIGRATIONS, connect, migration_files
from app.git import git
from app.ingest import CloneLimits, SourceSpec, clone_and_select
from app.sources import Source
from app.text_lines import physical_lines
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
        ("GET", "/api/search?q=deploy"),
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


def verify_indexed_file_reads(
    client: httpx.Client,
    source: Source,
    settings: Settings,
    observations: dict[str, object],
) -> Source:
    spec = SourceSpec(source.id, source.url)
    limits = CloneLimits(100, 10_000_000, 30)
    repository = Path(source.url)
    deploy = repository / "docs/deploy.md"
    deploy.write_bytes(deploy.read_bytes() + b"\nRange line 17\nRange line 18\nTail\n")
    git(repository, "add", "--force", ".")
    git(repository, "commit", "-m", "Prepare five-line fetch range")
    first = clone_and_select(spec, settings=settings, limits=limits)
    with connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE sources SET indexed_commit = %s WHERE id = %s",
            (first.commit_sha, source.id),
        )
    url = f"/api/sources/{source.id}/file"
    expected = next(
        file.content for file in first.files if file.path == "docs/deploy.md"
    )
    renamed_content = next(
        file.content for file in first.files if file.path == "docs/onboarding.md"
    )
    full = client.get(url, params={"path": "docs/deploy.md"})
    assert full.status_code == 200
    assert full.json()["content"] == expected
    assert full.json()["commit"] == first.commit_sha
    (repository / "docs/deploy.md").write_bytes(b"Updated deployment at C2\n")
    (repository / "docs/legacy-deploy.md").unlink()
    (repository / "docs/onboarding.md").rename(repository / "docs/start.md")
    git(repository, "add", "--force", ".")
    git(repository, "commit", "-m", "Update smoke handbook")
    second = clone_and_select(spec, settings=settings, limits=limits)
    (repository / "docs/deploy.md").write_bytes(b"Uncommitted working content")
    assert client.get(url, params={"path": "docs/deploy.md"}).json() == full.json()
    window = client.get(url, params={"path": "docs/deploy.md", "start": 14, "end": 18})
    assert window.status_code == 200
    assert window.json()["content"] == "".join(physical_lines(expected)[13:18])
    assert len(physical_lines(window.json()["content"])) == 5
    assert window.json()["commit"] == first.commit_sha
    assert (window.json()["start_line"], window.json()["end_line"]) == (14, 18)
    clipped = client.get(url, params={"path": "docs/deploy.md", "end": 999})
    assert clipped.status_code == 200 and clipped.json() == full.json()
    for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
        old = client.get(url, params={"path": path})
        assert old.status_code == 200 and old.json()["commit"] == first.commit_sha
    with connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE sources SET indexed_commit = %s WHERE id = %s",
            (second.commit_sha, source.id),
        )
    for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
        assert client.get(url, params={"path": path}).status_code == 404
    renamed = client.get(url, params={"path": "docs/start.md"})
    assert renamed.status_code == 200 and renamed.json()["commit"] == second.commit_sha
    assert renamed.json()["content"] == renamed_content
    edited = client.get(url, params={"path": "docs/deploy.md"})
    assert edited.status_code == 200
    assert edited.json()["content"] == "Updated deployment at C2\n"
    assert edited.json()["commit"] == second.commit_sha
    observations["indexed_file_reads"] = {
        "setup": "acquisition library and indexed_commit record updates; no worker",
        "source_id": str(source.id),
        "commit_c": first.commit_sha,
        "commit_c2": second.commit_sha,
        "full_file_at_c": True,
        "range_at_c": [14, 18],
        "range_response": window.json(),
        "eof_clipped": True,
        "pinned_after_acquiring_c2": True,
        "deleted_and_renamed_old_paths": 404,
        "new_path_status": 200,
        "renamed_response": renamed.json(),
        "updated_content_at_c2": True,
    }
    return Source.model_validate(client.get(f"/api/sources/{source.id}").json())


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
            source = verify_indexed_file_reads(
                client,
                source,
                Settings(
                    database_url,
                    Path(state),
                    models_mode="fake",
                    allow_local_sources=True,
                ),
                observations,
            )
            second = register_handbook(client, Path(state))
            assert source.id != second.id
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
            file = client.get(
                f"/api/sources/{source.id}/file", params={"path": "docs/deploy.md"}
            )
            assert file.status_code == 200
            assert file.json()["commit"] == source.indexed_commit
            assert file.json()["content"] == "Updated deployment at C2\n"
            observations["indexed_file_after_restart"] = {
                "status": 200,
                "commit": file.json()["commit"],
                "content_matches": True,
            }
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
