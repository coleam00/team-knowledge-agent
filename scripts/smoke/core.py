"""Shared smoke machinery: the owned app process, readiness, the built page, handbook
registration, the migration ledger, and the context every feature check receives.

Feature checks never live here. Each feature owns one module in
`scripts/smoke/features/` (see that package's docstring)."""

import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import httpx

from app.config import Settings
from app.db import MIGRATIONS, connect, migration_files
from app.ingest._ownership import ProcessTree
from app.sources import Source
from tests.handbook import create_handbook_repo

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Context:
    """What one smoke run shares with every feature check.

    `client` is replaced for the restarted app; `sources` lists every source the
    features registered, in order, and the core checks the listing against it.
    `data` carries a feature's own values from before the restart to after it."""

    client: httpx.Client
    state: Path
    database_url: str
    settings: Settings
    observations: dict[str, object]
    sources: list[Source] = field(default_factory=list)
    data: dict[str, object] = field(default_factory=dict)


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
        tree = ProcessTree(
            command, ROOT, environment, stdout=log.fileno(), stderr=log.fileno()
        )
        process = tree.proc
        details: dict[str, object] = {"pid": process.pid, "argv": command}
        observations[label] = details
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
            ) as client:
                yield process, client
        finally:
            tree.close()
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
    observed = Source.model_validate(client.get(f"/api/sources/{source.id}").json())
    assert observed.status in {"queued", "indexing", "ready"}
    if observed.status != "ready":
        assert observed == source.model_copy(update={"status": observed.status})
    else:
        assert (
            observed.id,
            observed.url,
            observed.owner,
            observed.name,
            observed.created_at,
        ) == (source.id, source.url, source.owner, source.name, source.created_at)
        assert (
            observed.indexed_commit and observed.indexed_at and observed.error is None
        )
    duplicate = client.post("/api/sources", json={"url": str(repository)})
    assert duplicate.status_code == 409
    assert duplicate.json()["id"] == str(source.id)
    invalid = client.post(
        "/api/sources", json={"url": "https://gitlab.com/acme/handbook"}
    )
    assert invalid.status_code == 422
    # All feature fixtures must finish ingestion before manual snapshot setup.
    # Import here to avoid a cycle with the feature's Context dependency.
    from scripts.smoke.features.f15_ingest import wait_for_source

    return wait_for_source(client, source)


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
