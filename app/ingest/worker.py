import logging
import tempfile
from collections.abc import Iterator
from threading import Event
from typing import Protocol

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from app import jobs
from app.chunking import chunk_text
from app.config import Settings
from app.index_store import ChunkInput, replace_file
from app.ingest.contracts import CloneLimits, CloneSelectError, SourceSpec
from app.ingest.repository import clone_and_select
from app.models import Embedder, get_embedder

logger = logging.getLogger(__name__)
SHUTDOWN = "Indexing was interrupted by application shutdown."


class IngestError(Exception):
    pass


class Spool(Protocol):
    def write(self, text: str, /) -> int: ...
    def seek(self, offset: int, /) -> int: ...
    def __iter__(self) -> Iterator[str]: ...


class PreparedFile(BaseModel):
    path: str
    blob_sha: str
    language: str
    chunks: list[ChunkInput]


def check_stop(stop: Event) -> None:
    if stop.is_set():
        raise IngestError(SHUTDOWN)


def prepare(
    job: jobs.Job, settings: Settings, stop: Event, embedder: Embedder, spool: Spool
) -> str:
    result = clone_and_select(
        SourceSpec(job.source_id, job.url),
        settings=settings,
        limits=CloneLimits(
            settings.ingest_max_files,
            settings.ingest_max_total_bytes,
            settings.ingest_command_timeout_seconds,
        ),
    )
    check_stop(stop)
    for file in result.files:
        chunks = []
        for chunk in chunk_text(
            file.path,
            file.content,
            kind="markdown" if file.language == "markdown" else "text",
            window_lines=settings.chunk_window_lines,
            overlap_lines=settings.chunk_overlap_lines,
        ):
            check_stop(stop)
            try:
                chunks.append(
                    ChunkInput(
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                        embedding=tuple(embedder.embed(chunk.content)),
                        embedding_model=embedder.model_id,
                    )
                )
            except Exception:
                raise IngestError("Embedding preparation failed.") from None
        prepared = PreparedFile(
            path=file.path,
            blob_sha=file.blob_sha,
            language=file.language,
            chunks=chunks,
        )
        spool.write(prepared.model_dump_json() + "\n")
    spool.seek(0)
    return result.commit_sha


def publish(
    connection: psycopg.Connection[TupleRow],
    job: jobs.Job,
    commit: str,
    spool: Spool,
) -> None:
    with connection.transaction():
        if jobs.locked_states(connection, job) != ("indexing", "indexing"):
            raise IngestError(jobs.INELIGIBLE)
        eligible = connection.execute(
            "SELECT indexed_commit IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM files WHERE source_id = s.id) "
            "AND NOT EXISTS (SELECT 1 FROM chunks WHERE source_id = s.id) "
            "FROM sources s WHERE id = %s",
            (job.source_id,),
        ).fetchone()
        if eligible != (True,):
            raise IngestError(jobs.INELIGIBLE)
        for record in spool:
            file = PreparedFile.model_validate_json(record)
            replace_file(
                connection,
                source_id=job.source_id,
                path=file.path,
                blob_sha=file.blob_sha,
                language=file.language,
                chunks=file.chunks,
            )
        connection.execute(
            "UPDATE sources SET status = 'ready', indexed_commit = %s, "
            "indexed_at = clock_timestamp(), error = NULL, "
            "file_count = (SELECT count(*) FROM files WHERE source_id = %s), "
            "chunk_count = (SELECT count(*) FROM chunks WHERE source_id = %s) "
            "WHERE id = %s",
            (commit, job.source_id, job.source_id, job.source_id),
        )
        connection.execute(
            "UPDATE jobs SET status = 'ready', finished_at = clock_timestamp() "
            "WHERE id = %s",
            (job.id,),
        )


def execute(
    connection: psycopg.Connection[TupleRow],
    job: jobs.Job,
    settings: Settings,
    stop: Event,
) -> None:
    try:
        check_stop(stop)
        embedder = get_embedder(settings)
        with tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", dir=settings.data_dir
        ) as spool:
            commit = prepare(job, settings, stop, embedder, spool)
            check_stop(stop)
            publish(connection, job, commit, spool)
    except Exception as error:
        logger.warning(
            "Indexing failed source=%s job=%s (%s)",
            job.source_id,
            job.id,
            type(error).__name__,
        )
        if isinstance(error, CloneSelectError):
            message = str(CloneSelectError(error.code))
        elif isinstance(error, IngestError):
            message = str(error)
        elif isinstance(error, (psycopg.Error, OSError)):
            message = "Index storage failed."
        else:
            message = "Index preparation failed."
        jobs.fail(connection, job, message)


def run_once(settings: Settings, stop: Event) -> bool:
    for job in jobs.active_jobs(settings.database_url):
        if stop.is_set():
            break
        with jobs.ownership(settings.database_url, job.source_id) as connection:
            if (
                connection is not None
                and not stop.is_set()
                and jobs.claim(connection, job)
            ):
                execute(connection, job, settings, stop)
                return True
    return False


def run_worker(settings: Settings, stop: Event) -> None:
    while not stop.is_set():
        try:
            worked = run_once(settings, stop)
        except Exception as error:
            logger.warning("Ingest worker unavailable (%s)", type(error).__name__)
            stop.wait(1)
        else:
            if not worked:
                stop.wait(0.25)
