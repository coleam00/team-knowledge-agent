import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from app import db, jobs

pytestmark = pytest.mark.database


def insert_source(database_url: str, url: str = "https://github.com/acme/repo") -> UUID:
    source_id = uuid4()
    with db.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO sources (id, url, owner, name) "
            "VALUES (%s, %s, 'acme', 'repo')",
            (source_id, url),
        )
        jobs.enqueue(connection, source_id)
    return source_id


def test_upgrade_backfills_only_queued_once(database_url: str, tmp_path: Path) -> None:
    for version, path in db.migration_files(db.MIGRATIONS):
        if version < 6:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    db.run_migrations(database_url, tmp_path)
    with db.connect(database_url) as connection:
        for status in ("queued", "ready", "failed"):
            connection.execute(
                "INSERT INTO sources (id, url, owner, name, status) "
                "VALUES (%s, %s, 'acme', 'repo', %s)",
                (uuid4(), status, status),
            )
        before = connection.execute("SELECT * FROM sources ORDER BY id").fetchall()
    db.run_migrations(database_url)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert (
            connection.execute("SELECT * FROM sources ORDER BY id").fetchall() == before
        )
        assert connection.execute(
            "SELECT s.status, j.kind, j.status, started_at, finished_at, j.error "
            "FROM jobs j JOIN sources s ON s.id = j.source_id"
        ).fetchall() == [("queued", "index", "queued", None, None, None)]


@pytest.mark.parametrize(
    "assignment",
    [
        "kind = 'reindex'",
        "status = 'unknown'",
        "status = 'indexing'",
        "started_at = clock_timestamp()",
        "finished_at = clock_timestamp()",
        "error = 'bad'",
        "status = 'ready', started_at = clock_timestamp()",
        "status = 'failed', started_at = clock_timestamp(), "
        "finished_at = clock_timestamp()",
        "status = 'failed', started_at = clock_timestamp(), "
        "finished_at = clock_timestamp(), error = '   '",
        "status = 'ready', started_at = clock_timestamp(), "
        "finished_at = clock_timestamp() - interval '1 second'",
    ],
)
def test_job_state_constraints(database_url: str, assignment: str) -> None:
    db.run_migrations(database_url)
    insert_source(database_url)
    with pytest.raises(psycopg.errors.CheckViolation), db.connect(database_url) as conn:
        conn.execute("UPDATE jobs SET " + assignment)


def test_concurrent_active_inserts_and_terminal_history(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id = insert_source(database_url)
    job = jobs.active_jobs(database_url)[0]
    with jobs.ownership(database_url, source_id) as conn:
        assert conn is not None
        jobs.fail(conn, job, "Test failure")
    barrier = Barrier(2)

    def enqueue() -> bool:
        with db.connect(database_url) as conn:
            barrier.wait(5)
            try:
                jobs.enqueue(conn, source_id)
            except psycopg.errors.UniqueViolation:
                return False
            return True

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: enqueue(), range(2)))
    assert sorted(results) == [False, True]
    with db.connect(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone() == (2,)


def test_process_owner_cannot_be_stolen_and_orphan_fails(
    database_url: str, tmp_path: Path
) -> None:
    db.run_migrations(database_url)
    source_id = insert_source(database_url)
    signal = tmp_path / "claimed"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    code = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from app import jobs\n"
        "url = os.environ['DATABASE_URL']\n"
        "job = jobs.active_jobs(url)[0]\n"
        "with jobs.ownership(url, job.source_id) as conn:\n"
        "    assert conn is not None and jobs.claim(conn, job)\n"
        "    Path(sys.argv[1]).write_text('claimed')\n"
        "    time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(signal)], env=environment
    )
    try:
        deadline = time.monotonic() + 10
        while not signal.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert signal.exists()
        job = jobs.active_jobs(database_url)[0]
        assert job.status == "indexing"
        with jobs.ownership(database_url, source_id) as conn:
            assert conn is None
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        assert process.poll() is not None
    with jobs.ownership(database_url, source_id) as conn:
        assert conn is not None
        assert not jobs.claim(conn, job)
        assert conn.execute(
            "SELECT status, error, started_at <= finished_at FROM jobs"
        ).fetchone() == ("failed", jobs.INTERRUPTED, True)
        assert conn.execute("SELECT status, error FROM sources").fetchone() == (
            "failed",
            jobs.INTERRUPTED,
        )


def test_late_failure_preserves_ready_job(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id = insert_source(database_url)
    job = jobs.active_jobs(database_url)[0]
    with jobs.ownership(database_url, source_id) as conn:
        assert conn is not None and jobs.claim(conn, job)
        with conn.transaction():
            conn.execute("UPDATE sources SET status = 'ready'")
            conn.execute(
                "UPDATE jobs SET status = 'ready', finished_at = clock_timestamp()"
            )
        jobs.fail(conn, job, "Ambiguous commit result")
        assert conn.execute("SELECT status, error FROM jobs").fetchone() == (
            "ready",
            None,
        )
        assert conn.execute("SELECT status, error FROM sources").fetchone() == (
            "ready",
            None,
        )
