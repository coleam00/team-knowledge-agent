import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, main
from app.features import f15_ingest

ClientFactory = Callable[[FastAPI], TestClient]


def wait_for_health(client: TestClient, status: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get("/api/health").status_code == status:
            return
        time.sleep(0.01)
    pytest.fail(f"Health did not reach {status}")


def test_health_waits_for_initialization_and_recovers_after_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entered, release = threading.Event(), threading.Event()

    def migrate(_: str | None) -> None:
        entered.set()
        assert release.wait(5)

    migration = Mock(side_effect=migrate)
    probe = Mock()
    monkeypatch.setattr(db, "run_migrations", migration)
    monkeypatch.setattr(db, "probe", probe)
    with client_factory(main.create_app()) as client:
        try:
            assert entered.wait(5)
            response = client.get("/api/health")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable", "db": "unavailable"}
            probe.assert_not_called()
        finally:
            release.set()
        wait_for_health(client, 200)
        assert client.get("/api/health").json() == {"status": "ok", "db": "ok"}
        probe.side_effect = RuntimeError("postgresql://user:probe-secret@host/database")
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable", "db": "unavailable"}
        assert "probe-secret" not in response.text
        assert "Database probe failed (RuntimeError)" in caplog.text
        assert "probe-secret" not in caplog.text
        probe.side_effect = None
        assert client.get("/api/health").status_code == 200
    migration.assert_called_once()


def test_initialization_failure_is_sticky_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    migration = Mock(side_effect=RuntimeError("credential-sentinel"))
    worker = Mock()
    monkeypatch.setattr(f15_ingest, "run_worker", worker)
    monkeypatch.setattr(db, "run_migrations", migration)
    with client_factory(main.create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert "credential-sentinel" not in response.text
        assert client.get("/api/build-id").status_code == 200
    migration.assert_called_once()
    worker.assert_not_called()
    assert "Initialization failed (RuntimeError)" in caplog.text
    assert "credential-sentinel" not in caplog.text


def test_data_directory_failure_prevents_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
) -> None:
    file = tmp_path / "file"
    file.write_text("not a directory")
    monkeypatch.setenv("TKA_DATA_DIR", str(file))
    migrate = Mock()
    monkeypatch.setattr(db, "run_migrations", migrate)
    with client_factory(main.create_app()) as client:
        assert client.get("/api/health").status_code == 503
    migrate.assert_not_called()


def test_directory_resolution_failure_keeps_http_available(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        Path, "expanduser", Mock(side_effect=RuntimeError("credential-sentinel"))
    )
    with client_factory(main.create_app()) as client:
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/build-id").status_code == 200
    assert "Configuration failed (RuntimeError)" in caplog.text
    assert "credential-sentinel" not in caplog.text


def test_shutdown_waits_for_initialization(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
) -> None:
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def migrate(_: str | None) -> None:
        entered.set()
        assert release.wait(5)
        finished.set()

    monkeypatch.setattr(db, "run_migrations", migrate)
    worker = Mock()
    monkeypatch.setattr(f15_ingest, "run_worker", worker)
    client = client_factory(main.create_app())
    client.__enter__()
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as workers:
            closing = workers.submit(client.__exit__, None, None, None)
            try:
                with pytest.raises(TimeoutError):
                    closing.result(timeout=0.05)
            finally:
                release.set()
            closing.result(timeout=5)
            assert finished.is_set()
            worker.assert_not_called()
    finally:
        release.set()


def test_lifespan_starts_worker_after_migrations_and_joins_it(
    monkeypatch: pytest.MonkeyPatch, client_factory: ClientFactory
) -> None:
    migrated, entered, finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    threads = []

    def run_worker(settings: object, stop: threading.Event) -> None:
        assert migrated.is_set()
        threads.append(threading.current_thread())
        entered.set()
        assert stop.wait(5)
        finished.set()

    monkeypatch.setattr(db, "run_migrations", lambda _: migrated.set())
    monkeypatch.setattr(f15_ingest, "run_worker", run_worker)
    with client_factory(main.create_app()):
        assert entered.wait(5)
        assert not finished.is_set()
    assert finished.is_set()
    assert len(threads) == 1 and not threads[0].is_alive()


def test_worker_start_failure_does_not_join_an_unstarted_thread(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    thread = Mock()
    thread.start.side_effect = RuntimeError("credential-sentinel")
    monkeypatch.setattr(db, "probe", Mock())
    monkeypatch.setattr(f15_ingest, "Thread", Mock(return_value=thread))
    monkeypatch.setattr(db, "run_migrations", Mock())
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        deadline = time.monotonic() + 5
        while not thread.start.called and time.monotonic() < deadline:
            time.sleep(0.01)
        assert thread.start.called
    assert thread.start.call_count >= 1
    thread.join.assert_not_called()
    assert "Worker startup failed (RuntimeError)" in caplog.text
    assert "credential-sentinel" not in caplog.text


@pytest.mark.parametrize("candidate", ["  candidate\n", "", "révision"])
def test_http_candidate_is_exact_and_cached(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    candidate: str,
) -> None:
    monkeypatch.setenv("FACTORY_RUNTIME_CANDIDATE", candidate)
    application = main.create_app()
    monkeypatch.setenv("FACTORY_RUNTIME_CANDIDATE", "changed")
    with client_factory(application) as client:
        response = client.get("/api/build-id")
        assert response.status_code == 200
        assert response.content == candidate.encode("utf-8")
        assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_http_git_identity(client_factory: ClientFactory) -> None:
    expected = subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=3).strip()
    with client_factory(main.create_app()) as client:
        assert client.get("/api/build-id").content == expected


def test_http_unknown_identity(
    monkeypatch: pytest.MonkeyPatch, client_factory: ClientFactory
) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=FileNotFoundError()))
    with client_factory(main.create_app()) as client:
        assert client.get("/api/build-id").text == "unknown"


def test_missing_web_build_and_unbuilt_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
) -> None:
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    with client_factory(main.create_app()) as client:
        assert client.get("/").status_code == 503
        assert "build_web.py" in client.get("/").text
        assert client.get("/api/sources").status_code == 503
        assert client.get("/api/search?q=deploy").status_code == 503
        for path in ["/mcp", "/assets/missing.js"]:
            assert client.get(path).status_code == 404
        assert client.post("/api/ask", json={"question": "hello"}).status_code == 503


def test_serves_actual_html_and_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text(
        "document.title = 'Team Knowledge Agent';"
    )
    (tmp_path / "index.html").write_text("<title>Team Knowledge Agent</title>")
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    with client_factory(main.create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == "<title>Team Knowledge Agent</title>"
        assert response.headers["content-type"].startswith("text/html")
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "document.title = 'Team Knowledge Agent';"
        assert "javascript" in asset.headers["content-type"]
