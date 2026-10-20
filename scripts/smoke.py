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

from app.db import connect

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
        ("GET", "/api/sources"),
        ("POST", "/api/sources"),
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


def ledger(database_url: str) -> list[dict[str, object]]:
    with connect(database_url) as connection:
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        ).fetchone() == ("vector",)
        rows = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in rows] == [1]
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
            observations["unbuilt_features"] = absent_features(client)
            before = ledger(database_url)
            observations["ledger_before_restart"] = before
        with running_app(environment, observations, "restart") as (process, client):
            wait_for_health(process, client, candidate, 200)
            after = ledger(database_url)
            assert after == before
            observations["ledger_after_restart"] = after
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
