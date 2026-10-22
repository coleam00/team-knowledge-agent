import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import TupleRow, dict_row
from pydantic import BaseModel

from app import db

LOCK_NAMESPACE = 0x544B4102
INTERRUPTED = "Indexing was interrupted; its database owner disconnected."
INELIGIBLE = "Source is not eligible for initial indexing."


class Job(BaseModel):
    id: UUID
    source_id: UUID
    url: str
    status: Literal["queued", "indexing", "ready", "failed"]


def enqueue(connection: psycopg.Connection[TupleRow], source_id: UUID) -> None:
    connection.execute(
        "INSERT INTO jobs (id, source_id, kind) VALUES (%s, %s, 'index')",
        (uuid4(), source_id),
    )


def lock_key(source_id: UUID) -> int:
    return int.from_bytes(hashlib.sha256(source_id.bytes).digest()[:4], signed=True)


def active_jobs(database_url: str | None) -> list[Job]:
    with db.connect(database_url) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT j.id, j.source_id, j.status, s.url FROM jobs j "
                "JOIN sources s ON s.id = j.source_id "
                "WHERE j.status IN ('queued', 'indexing') "
                "ORDER BY s.created_at, j.id"
            )
            return [Job.model_validate(row) for row in cursor.fetchall()]


@contextmanager
def ownership(
    database_url: str | None, source_id: UUID
) -> Iterator[psycopg.Connection[TupleRow] | None]:
    with db.connect(database_url) as connection:
        connection.autocommit = True
        key = lock_key(source_id)
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", (LOCK_NAMESPACE, key)
        ).fetchone()
        if acquired != (True,):
            yield None
            return
        try:
            yield connection
        finally:
            if not connection.closed:
                try:
                    connection.execute(
                        "SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, key)
                    )
                except psycopg.Error:
                    connection.close()


def locked_states(
    connection: psycopg.Connection[TupleRow], job: Job
) -> tuple[str, str] | None:
    row = connection.execute(
        "SELECT status FROM jobs WHERE id = %s AND source_id = %s FOR UPDATE",
        (job.id, job.source_id),
    ).fetchone()
    if row is None:
        return None
    source = connection.execute(
        "SELECT status FROM sources WHERE id = %s FOR UPDATE", (job.source_id,)
    ).fetchone()
    assert source is not None
    return str(row[0]), str(source[0])


def fail(connection: psycopg.Connection[TupleRow], job: Job, message: str) -> None:
    with connection.transaction():
        states = locked_states(connection, job)
        if states is None or states[0] not in {"queued", "indexing"}:
            return
        connection.execute(
            "UPDATE jobs SET status = 'failed', "
            "started_at = COALESCE(started_at, clock_timestamp()), "
            "finished_at = clock_timestamp(), error = %s WHERE id = %s",
            (message, job.id),
        )
        if states[1] in {"queued", "indexing"}:
            connection.execute(
                "UPDATE sources SET status = 'failed', error = %s WHERE id = %s",
                (message, job.source_id),
            )


def claim(connection: psycopg.Connection[TupleRow], job: Job) -> bool:
    with connection.transaction():
        states = locked_states(connection, job)
        if states is None or states[0] not in {"queued", "indexing"}:
            return False
        if states[0] == "indexing":
            fail(connection, job, INTERRUPTED)
            return False
        if states[1] != "queued":
            fail(connection, job, INELIGIBLE)
            return False
        connection.execute(
            "UPDATE jobs SET status = 'indexing', started_at = clock_timestamp() "
            "WHERE id = %s",
            (job.id,),
        )
        connection.execute(
            "UPDATE sources SET status = 'indexing', error = NULL WHERE id = %s",
            (job.source_id,),
        )
        return True
