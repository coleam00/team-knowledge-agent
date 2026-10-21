import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import StringIO
from pathlib import Path
from threading import Event, Lock
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from app import db, jobs, main
from app.chunking import chunk_text
from app.config import Settings
from app.git import git
from app.index_store import ChunkInput, replace_file
from app.ingest import worker
from app.ingest.contracts import SelectedFile, SelectionResult, SourceSpec
from app.models.fake import FakeEmbedder
from app.sources import Source
from tests.handbook import ORIGIN, create_handbook_repo
from tests.test_http import ClientFactory, wait_for_health
from tests.test_jobs import insert_source

pytestmark = pytest.mark.database


class BlockingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.lock = Lock()
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        with self.lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            assert self.release.wait(90), "Test embedder was not released"
        return super().embed(text)


def wait_source(client: TestClient, source_id: UUID, status: str = "ready") -> Source:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        source = Source.model_validate(client.get(f"/api/sources/{source_id}").json())
        if source.status == status:
            return source
        assert source.status != "failed", source.error
        time.sleep(0.02)
    pytest.fail(f"Source did not reach {status}")


def assert_unpublished(database_url: str, source_id: UUID) -> None:
    with db.connect(database_url) as conn:
        assert conn.execute(
            "SELECT indexed_commit, indexed_at, file_count, chunk_count "
            "FROM sources WHERE id = %s",
            (source_id,),
        ).fetchone() == (None, None, 0, 0)
        for table in ("files", "chunks"):
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE source_id = %s", (source_id,)
            ).fetchone() == (0,)


def snapshot(database_url: str) -> list[list[TupleRow]]:
    with db.connect(database_url) as conn:
        return [
            conn.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()
            for table, key in (
                ("sources", "id"),
                ("files", "id"),
                ("chunks", "id"),
                ("jobs", "id"),
                ("schema_migrations", "version"),
            )
        ]


@pytest.fixture
def settings(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Settings:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", "1")
    result = Settings.from_env()
    result.data_dir.mkdir()
    db.run_migrations(database_url)
    return result


@pytest.mark.parametrize("window,overlap", [(80, 10), (4, 1)])
def test_http_async_complete_snapshot_two_workers_and_restart(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
    window: int,
    overlap: int,
) -> None:
    monkeypatch.setenv("TKA_CHUNK_WINDOW_LINES", str(window))
    monkeypatch.setenv("TKA_CHUNK_OVERLAP_LINES", str(overlap))
    repository = create_handbook_repo(tmp_path)
    other = create_handbook_repo(tmp_path)
    embedder = BlockingEmbedder()
    monkeypatch.setattr(worker, "get_embedder", lambda _: embedder)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        try:
            response = client.post("/api/sources", json={"url": str(repository)})
            assert response.status_code == 202
            queued = Source.model_validate(response.json())
            assert queued.status == "queued"
            assert embedder.entered.wait(30)
            indexing = wait_source(client, queued.id, "indexing")
            assert indexing.indexed_commit is None
            assert_unpublished(settings.database_url or "", queued.id)
            with db.connect(settings.database_url) as conn:
                assert conn.execute(
                    "SELECT status, started_at IS NOT NULL, finished_at, error "
                    "FROM jobs"
                ).fetchone() == ("indexing", True, None, None)
            with client_factory(main.create_app()) as second_worker:
                wait_for_health(second_worker, 200)
                second = Source.model_validate(
                    second_worker.post("/api/sources", json={"url": str(other)}).json()
                )
                assert wait_source(second_worker, second.id).file_count == 5
                assert wait_source(client, queued.id, "indexing").status == "indexing"
        finally:
            embedder.release.set()
        ready = wait_source(client, queued.id)
        assert ready.indexed_commit == git(repository, "rev-parse", "HEAD")
        expected = []
        with db.connect(settings.database_url) as conn:
            files = conn.execute(
                "SELECT path, language FROM files WHERE source_id = %s ORDER BY path",
                (ready.id,),
            ).fetchall()
            assert len(files) == ready.file_count == 5
            for path, language in files:
                text = (repository / path).read_bytes().decode("utf-8")
                expected.extend(
                    chunk_text(
                        path,
                        text,
                        kind="markdown" if language == "markdown" else "text",
                        window_lines=window,
                        overlap_lines=overlap,
                    )
                )
            chunks = conn.execute(
                "SELECT path, start_line, end_line, content, embedding::real[], "
                "embedding_model FROM chunks WHERE source_id = %s "
                "ORDER BY path, start_line",
                (ready.id,),
            ).fetchall()
            assert len(chunks) == ready.chunk_count == len(expected)
            for stored, wanted in zip(chunks, expected, strict=True):
                assert stored[:4] == (
                    wanted.path,
                    wanted.start_line,
                    wanted.end_line,
                    wanted.content,
                )
                assert stored[4] == pytest.approx(embedder.embed(wanted.content))
                assert stored[5] == embedder.model_id
            assert conn.execute(
                "SELECT started_at <= %s AND %s <= finished_at FROM jobs "
                "WHERE source_id = %s",
                (ready.indexed_at, ready.indexed_at, ready.id),
            ).fetchone() == (True,)
        before = snapshot(settings.database_url or "")
    (repository / "docs/deploy.md").write_text("changed upstream\n")
    git(repository, "add", "docs/deploy.md")
    git(repository, "commit", "-m", "Upstream edit")
    with client_factory(main.create_app()) as restarted:
        wait_for_health(restarted, 200)
        assert wait_source(restarted, ready.id) == ready
        assert snapshot(settings.database_url or "") == before


def test_queued_job_survives_lifespan_and_runs_after_restart(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
) -> None:
    repository = create_handbook_repo(tmp_path)
    with monkeypatch.context() as held:
        held.setattr(main, "run_worker", lambda settings, stop: stop.wait(10))
        with client_factory(main.create_app()) as client:
            wait_for_health(client, 200)
            source = Source.model_validate(
                client.post("/api/sources", json={"url": str(repository)}).json()
            )
            with db.connect(settings.database_url) as conn:
                job_id = conn.execute("SELECT id FROM jobs").fetchone()
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        assert wait_source(client, source.id).file_count == 5
        with db.connect(settings.database_url) as conn:
            assert conn.execute("SELECT id FROM jobs").fetchall() == [job_id]


@pytest.mark.parametrize("failure", ["clone", "embedding", "publication"])
def test_failures_are_atomic_safe_and_worker_continues(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    repository = create_handbook_repo(tmp_path)
    if failure == "clone":
        repository = tmp_path / "bare"
        repository.mkdir()
        git(repository, "init", "--bare", "--template=")
        git(repository, "remote", "add", "origin", ORIGIN)
    if failure == "embedding":

        class FailingEmbedder(FakeEmbedder):
            calls = 0

            def embed(self, text: str) -> list[float]:
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("credential-sentinel")
                return super().embed(text)

        embedder = FailingEmbedder()
        monkeypatch.setattr(worker, "get_embedder", lambda _: embedder)
    if failure == "publication":
        original = replace_file
        calls = 0

        def fail_write(
            connection: psycopg.Connection[TupleRow],
            *,
            source_id: UUID,
            path: str,
            blob_sha: str,
            language: str,
            chunks: list[ChunkInput],
        ) -> UUID:
            nonlocal calls
            calls += 1
            result = original(
                connection,
                source_id=source_id,
                path=path,
                blob_sha=blob_sha,
                language=language,
                chunks=chunks,
            )
            if calls == 2:
                assert_unpublished(settings.database_url or "", source_id)
                raise psycopg.DataError("credential-sentinel")
            return result

        monkeypatch.setattr(worker, "replace_file", fail_write)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        source = Source.model_validate(
            client.post("/api/sources", json={"url": str(repository)}).json()
        )
        failed = wait_source(client, source.id, "failed")
        assert failed.error and "credential-sentinel" not in failed.error
        assert_unpublished(settings.database_url or "", source.id)
        with db.connect(settings.database_url) as conn:
            assert conn.execute(
                "SELECT status, error, started_at <= finished_at FROM jobs"
            ).fetchone() == ("failed", failed.error, True)
        assert client.get("/api/health").status_code == 200
        other = create_handbook_repo(tmp_path)
        second = Source.model_validate(
            client.post("/api/sources", json={"url": str(other)}).json()
        )
        assert wait_source(client, second.id).file_count == 5
    assert "credential-sentinel" not in caplog.text


@pytest.mark.parametrize("empty_file", [False, True])
def test_empty_selection_and_file(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, empty_file: bool
) -> None:
    source_id = insert_source(settings.database_url or "")
    files = (SelectedFile("empty.txt", "b" * 40, "text", 0, ""),) if empty_file else ()

    def select(source: SourceSpec, **kwargs: object) -> SelectionResult:
        return SelectionResult(source.source_id, ORIGIN, "a" * 40, files)

    monkeypatch.setattr(worker, "clone_and_select", select)
    assert worker.run_once(settings, Event())
    with db.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, indexed_commit, file_count, chunk_count FROM sources "
            "WHERE id = %s",
            (source_id,),
        ).fetchone() == ("ready", "a" * 40, int(empty_file), 0)


@pytest.mark.parametrize("limit", ["files", "bytes"])
def test_configured_limits_fail_explicitly(
    settings: Settings, tmp_path: Path, limit: str
) -> None:
    repository = create_handbook_repo(tmp_path)
    source_id = insert_source(settings.database_url or "", str(repository))
    limited = (
        replace(settings, ingest_max_files=2)
        if limit == "files"
        else replace(settings, ingest_max_total_bytes=1)
    )
    assert worker.run_once(limited, Event())
    assert_unpublished(settings.database_url or "", source_id)
    with db.connect(settings.database_url) as conn:
        row = conn.execute("SELECT status, error FROM sources").fetchone()
        assert row is not None and row[0] == "failed" and "limit" in row[1]


def test_disconnected_old_worker_cannot_publish(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = create_handbook_repo(tmp_path)
    source_id = insert_source(settings.database_url or "", str(repository))
    embedder = BlockingEmbedder()
    monkeypatch.setattr(worker, "get_embedder", lambda _: embedder)
    owner_pids = []
    original = jobs.claim

    def claim(conn: psycopg.Connection[TupleRow], job: jobs.Job) -> bool:
        result = original(conn, job)
        if result:
            owner_pids.append(conn.info.backend_pid)
        return result

    monkeypatch.setattr(jobs, "claim", claim)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(worker.run_once, settings, Event())
        try:
            assert embedder.entered.wait(10)
            with db.connect(settings.database_url) as conn:
                assert conn.execute(
                    "SELECT pg_terminate_backend(%s)", (owner_pids[0],)
                ).fetchone() == (True,)
            assert not worker.run_once(settings, Event())
        finally:
            embedder.release.set()
        with pytest.raises(psycopg.Error):
            running.result(timeout=10)
    assert_unpublished(settings.database_url or "", source_id)
    with db.connect(settings.database_url) as conn:
        assert conn.execute("SELECT status, error FROM jobs").fetchone() == (
            "failed",
            jobs.INTERRUPTED,
        )


def test_shutdown_fails_owned_work_and_preserves_unclaimed_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = insert_source(
        settings.database_url or "", str(create_handbook_repo(tmp_path))
    )
    second = insert_source(
        settings.database_url or "", str(create_handbook_repo(tmp_path))
    )
    embedder = BlockingEmbedder()
    monkeypatch.setattr(worker, "get_embedder", lambda _: embedder)
    stop = Event()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(worker.run_worker, settings, stop)
        try:
            assert embedder.entered.wait(10)
            stop.set()
        finally:
            embedder.release.set()
        running.result(timeout=10)
    assert_unpublished(settings.database_url or "", first)
    with db.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, error FROM jobs WHERE source_id = %s", (first,)
        ).fetchone() == ("failed", worker.SHUTDOWN)
        assert conn.execute(
            "SELECT status, started_at FROM jobs WHERE source_id = %s", (second,)
        ).fetchone() == ("queued", None)


@pytest.mark.parametrize("existing", ["commit", "file", "chunks"])
def test_initial_publication_preserves_unexpected_index_data(
    settings: Settings, existing: str
) -> None:
    source_id = insert_source(settings.database_url or "")
    with db.connect(settings.database_url) as conn:
        if existing == "commit":
            conn.execute(
                "UPDATE sources SET indexed_commit = %s WHERE id = %s",
                ("b" * 40, source_id),
            )
        else:
            replace_file(
                conn,
                source_id=source_id,
                path="existing.txt",
                blob_sha="b" * 40,
                language="text",
                chunks=[]
                if existing == "file"
                else [
                    ChunkInput(
                        start_line=1,
                        end_line=1,
                        content="preserved",
                        embedding=(1.0,),
                        embedding_model="test",
                    )
                ],
            )
    before = snapshot(settings.database_url or "")
    job = jobs.active_jobs(settings.database_url)[0]
    with jobs.ownership(settings.database_url, source_id) as conn:
        assert conn is not None and jobs.claim(conn, job)
        with pytest.raises(worker.IngestError, match="not eligible"):
            worker.publish(conn, job, "a" * 40, StringIO())
        jobs.fail(conn, job, jobs.INELIGIBLE)
    after = snapshot(settings.database_url or "")
    assert after[1:3] == before[1:3]
    with db.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, indexed_commit, error FROM sources"
        ).fetchone() == (
            "failed",
            "b" * 40 if existing == "commit" else None,
            jobs.INELIGIBLE,
        )
