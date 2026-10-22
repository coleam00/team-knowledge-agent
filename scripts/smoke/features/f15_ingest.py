"""HTTP background ingestion and durable snapshots, with exact chunk verification."""

import time
from pathlib import Path
from uuid import UUID

import httpx

from app.chunking import chunk_text
from app.config import Settings
from app.db import connect
from app.git import git
from app.models.fake import FakeEmbedder
from app.sources import Source
from scripts.smoke.core import Context, register_handbook


def wait_for_source(
    client: httpx.Client, source: Source, status: str = "ready"
) -> Source:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/sources/{source.id}")
        assert response.status_code == 200
        current = Source.model_validate(response.json())
        if current.status == status:
            return current
        assert current.status != "failed", current.error
        time.sleep(0.1)
    raise TimeoutError("Source did not finish indexing within 45 seconds")


def verify_snapshot(source: Source, settings: Settings) -> dict[str, object]:
    assert source.status == "ready"
    assert source.indexed_commit == git(Path(source.url), "rev-parse", "HEAD")
    embedder = FakeEmbedder()
    with connect(settings.database_url) as connection:
        files = connection.execute(
            "SELECT path, language FROM files WHERE source_id = %s ORDER BY path",
            (source.id,),
        ).fetchall()
        assert len(files) == source.file_count
        expected = []
        for path, language in files:
            text = (Path(source.url) / path).read_bytes().decode("utf-8")
            expected.extend(
                chunk_text(
                    path,
                    text,
                    kind="markdown" if language == "markdown" else "text",
                    window_lines=settings.chunk_window_lines,
                    overlap_lines=settings.chunk_overlap_lines,
                )
            )
        chunks = connection.execute(
            "SELECT path, start_line, end_line, content, embedding::real[], "
            "embedding_model FROM chunks WHERE source_id = %s "
            "ORDER BY path, start_line",
            (source.id,),
        ).fetchall()
        assert len(chunks) == source.chunk_count == len(expected)
        for stored, wanted in zip(chunks, expected, strict=True):
            assert stored[:4] == (
                wanted.path,
                wanted.start_line,
                wanted.end_line,
                wanted.content,
            )
            vector = embedder.embed(wanted.content)
            assert len(stored[4]) == len(vector)
            assert all(
                abs(a - b) < 1e-6 for a, b in zip(stored[4], vector, strict=True)
            )
            assert stored[5] == embedder.model_id
        job = connection.execute(
            "SELECT id, status, started_at, finished_at, error FROM jobs "
            "WHERE source_id = %s",
            (source.id,),
        ).fetchall()
        assert len(job) == 1
        job_id, status, started, finished, error = job[0]
        assert status == "ready" and error is None
        assert (
            source.indexed_at is not None and started <= source.indexed_at <= finished
        )
    return {
        "source": source.model_dump(mode="json"),
        "job_id": str(job_id),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "exact_chunks_and_vectors": True,
        "embedding_model": embedder.model_id,
    }


def index_rows(database_url: str, source_id: UUID) -> list[list[object]]:
    with connect(database_url) as connection:
        return [
            list(row)
            for row in connection.execute(
                "SELECT row_to_json(t)::text FROM ("
                "SELECT 'chunk' AS kind, to_jsonb(c) AS value FROM chunks c "
                "WHERE source_id = %s "
                "UNION ALL SELECT 'job', to_jsonb(j) FROM jobs j WHERE source_id = %s"
                ") t ORDER BY kind, value->>'id'",
                (source_id, source_id),
            )
        ]


def before_restart(ctx: Context) -> None:
    bare = ctx.state / "unsupported-bare"
    bare.mkdir()
    git(bare, "init", "--bare", "--template=")
    git(bare, "remote", "add", "origin", "https://github.com/tka-fixtures/bare")
    response = ctx.client.post("/api/sources", json={"url": str(bare)})
    assert response.status_code == 202
    failed = wait_for_source(
        ctx.client, Source.model_validate(response.json()), "failed"
    )
    assert failed.error and failed.indexed_commit is None
    assert failed.file_count == failed.chunk_count == 0
    assert ctx.client.get("/api/health").status_code == 200
    with connect(ctx.database_url) as connection:
        assert connection.execute(
            "SELECT status, error, started_at <= finished_at FROM jobs "
            "WHERE source_id = %s",
            (failed.id,),
        ).fetchone() == ("failed", failed.error, True)
    ctx.observations["failed_indexing"] = failed.model_dump(mode="json")
    ctx.sources.append(failed)
    source = register_handbook(ctx.client, ctx.state)
    assert source.file_count == 5
    ctx.observations["indexing"] = verify_snapshot(source, ctx.settings)
    ctx.sources.append(source)
    ctx.data["ingest_source"] = source
    ctx.data["ingest_rows"] = index_rows(ctx.database_url, source.id)


def after_restart(ctx: Context) -> None:
    source = ctx.data["ingest_source"]
    assert isinstance(source, Source)
    assert ctx.client.get(f"/api/sources/{source.id}").json() == source.model_dump(
        mode="json"
    )
    assert index_rows(ctx.database_url, source.id) == ctx.data["ingest_rows"]
    ctx.observations["indexing_after_restart"] = verify_snapshot(source, ctx.settings)
