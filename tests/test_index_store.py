import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import TupleRow

from app import db
from app.index_store import ChunkInput, full_text_search, replace_file

DEPLOY = "Deploys are frozen on Fridays after 14:00 UTC."


def seed_chunk(
    connection: psycopg.Connection[TupleRow],
    *,
    source_id: UUID,
    path: str = "docs/deploy.md",
    content: str = DEPLOY,
    chunk_id: UUID | None = None,
    embedding: tuple[float, ...] = (0.25, -0.5, 1.0),
    model: str = "fixture/model-v1",
) -> tuple[UUID, UUID]:
    file_id, chunk_id = uuid4(), chunk_id or uuid4()
    connection.execute(
        "INSERT INTO sources (id, url, owner, name) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        (source_id, f"https://github.com/fixtures/{source_id}", "fixtures", "handbook"),
    )
    row = connection.execute(
        "INSERT INTO files (id, source_id, path, blob_sha, language) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (source_id, path) DO UPDATE SET path = EXCLUDED.path RETURNING id",
        (file_id, source_id, path, "a" * 40, "markdown"),
    ).fetchone()
    assert row is not None
    file_id = UUID(str(row[0]))
    connection.execute(
        "INSERT INTO chunks (id, source_id, file_id, path, start_line, end_line, "
        "content, embedding, embedding_model) "
        "VALUES (%s, %s, %s, %s, 4, 6, %s, %s::vector, %s)",
        (chunk_id, source_id, file_id, path, content, json.dumps(embedding), model),
    )
    return file_id, chunk_id


def chunk(content: str = DEPLOY) -> ChunkInput:
    return ChunkInput(
        start_line=4,
        end_line=6,
        content=content,
        embedding=(0.25, -0.5, 1.0),
        embedding_model="fixture/model-v1",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"start_line": 0},
        {"end_line": 3},
        {"embedding": []},
        {"embedding": [float("nan")]},
        {"embedding": [float("inf")]},
        {"embedding_model": "   "},
    ],
)
def test_invalid_chunk_metadata(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ChunkInput.model_validate(chunk().model_dump() | changes)


@pytest.mark.database
@pytest.mark.parametrize(
    ("model", "accepted"),
    [
        ("", False),
        (" ", False),
        ("   ", False),
        ("fixture/model-v1", True),
        (" \tfixture/model-v1\n ", True),
        ("\u200b", True),
        ("\ufeff", True),
        *[
            (character, True)
            for character in (
                "\t\n\v\f\r\x1c\x1d\x1e\x1f\x85\xa0\u1680"
                "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
                "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
            )
        ],
    ],
)
def test_model_identity_sql_and_python_conformance(
    database_url: str, model: str, accepted: bool
) -> None:
    db.run_migrations(database_url)
    payload = chunk().model_dump() | {"embedding_model": model}
    with db.connect(database_url) as connection:
        _, valid_id = seed_chunk(connection, source_id=uuid4(), path="valid.md")
        if not accepted:
            with pytest.raises(ValueError):
                ChunkInput.model_validate(payload)
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    seed_chunk(connection, source_id=uuid4(), model=model)
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(
                        "UPDATE chunks SET embedding_model = %s", (model,)
                    )
        else:
            assert ChunkInput.model_validate(payload).embedding_model == model
            _, inserted_id = seed_chunk(connection, source_id=uuid4(), model=model)
            _, updated_id = seed_chunk(connection, source_id=uuid4())
            connection.execute(
                "UPDATE chunks SET embedding_model = %s WHERE id = %s",
                (model, updated_id),
            )
    with db.connect(database_url) as connection:
        matches = {match.id: match for match in full_text_search(connection, "deploys")}
        assert matches[valid_id].embedding_model == "fixture/model-v1"
        if accepted:
            assert set(matches) == {valid_id, inserted_id, updated_id}
            assert matches[inserted_id].embedding_model == model
            assert matches[updated_id].embedding_model == model
        else:
            assert set(matches) == {valid_id}


@pytest.mark.database
def test_round_trip_and_reconnect(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id = uuid4()
    text = "Deploys frozen\r\n\tCaf\u00e9 'quoted' <data>\r\nlast line"
    originals = [
        chunk(text),
        ChunkInput(
            start_line=12,
            end_line=13,
            content="Deploys frozen again\nA different schedule",
            embedding=(0.5, 0.125, -1.0),
            embedding_model="fixture/model-v2",
        ),
    ]
    with db.connect(database_url) as connection:
        file_id = replace_file(
            connection,
            source_id=source_id,
            path="docs/caf\u00e9.md",
            blob_sha="b" * 40,
            language="markdown",
            chunks=originals,
        )
        before = full_text_search(connection, "deploys frozen")
        assert len(before) == len(originals)
        assert {
            match.content: match.model_dump(include=set(ChunkInput.model_fields))
            for match in before
        } == {original.content: original.model_dump() for original in originals}
        assert len({match.id for match in before}) == len(originals)
        for match in before:
            assert match.file_id == file_id and match.source_id == source_id
            assert match.path == "docs/caf\u00e9.md"
            assert match.rank > 0
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert full_text_search(connection, "deploys frozen") == before
        assert connection.execute("SELECT * FROM files").fetchone() == (
            file_id,
            source_id,
            "docs/caf\u00e9.md",
            "b" * 40,
            "markdown",
        )


@pytest.mark.database
def test_replacement_isolated_and_empty_replacement_removes_old_matches(
    database_url: str,
) -> None:
    db.run_migrations(database_url)
    source_id, other_source = uuid4(), uuid4()
    with db.connect(database_url) as connection:
        file_id, old_chunk_id = seed_chunk(connection, source_id=source_id)
        seed_chunk(connection, source_id=other_source)
        seed_chunk(connection, source_id=source_id, path="other.md")
        other_files = connection.execute(
            "SELECT * FROM files WHERE id <> %s ORDER BY id", (file_id,)
        ).fetchall()
        other_chunks = connection.execute(
            "SELECT * FROM chunks WHERE file_id <> %s ORDER BY id", (file_id,)
        ).fetchall()
        replaced_id = replace_file(
            connection,
            source_id=source_id,
            path="docs/deploy.md",
            blob_sha="b" * 40,
            language="text",
            chunks=[chunk("Replacement lunar schedule")],
        )
        assert replaced_id == file_id
        assert (
            connection.execute(
                "SELECT 1 FROM chunks WHERE id = %s", (old_chunk_id,)
            ).fetchone()
            is None
        )
        assert connection.execute(
            "SELECT blob_sha, language FROM files WHERE id = %s", (file_id,)
        ).fetchone() == ("b" * 40, "text")
        assert len(full_text_search(connection, "lunar")) == 1
        assert len(full_text_search(connection, "deploys")) == 2
        replace_file(
            connection,
            source_id=source_id,
            path="docs/deploy.md",
            blob_sha="c" * 40,
            language="text",
            chunks=[],
        )
        assert full_text_search(connection, "lunar") == []
        assert (
            connection.execute(
                "SELECT * FROM files WHERE id <> %s ORDER BY id", (file_id,)
            ).fetchall()
            == other_files
        )
        assert (
            connection.execute(
                "SELECT * FROM chunks WHERE file_id <> %s ORDER BY id", (file_id,)
            ).fetchall()
            == other_chunks
        )


@pytest.mark.database
@pytest.mark.parametrize("outer_rollback", [False, True])
def test_replacement_failure_preserves_file_and_chunks(
    database_url: str, outer_rollback: bool
) -> None:
    db.run_migrations(database_url)
    source_id = uuid4()
    with db.connect(database_url) as connection:
        seed_chunk(connection, source_id=source_id)
        files = connection.execute("SELECT * FROM files").fetchall()
        chunks = connection.execute("SELECT * FROM chunks").fetchall()
    with db.connect(database_url) as connection:
        with pytest.raises(RuntimeError if outer_rollback else psycopg.DataError):
            with connection.transaction() if outer_rollback else nullcontext():
                replacements = [chunk("replacement")]
                if not outer_rollback:
                    replacements.append(
                        ChunkInput(
                            start_line=1,
                            end_line=1,
                            content="Too large for pgvector's float32 storage",
                            embedding=(1e100,),
                            embedding_model="fixture/model-v2",
                        )
                    )
                replace_file(
                    connection,
                    source_id=source_id,
                    path="docs/deploy.md",
                    blob_sha="b" * 40,
                    language="text",
                    chunks=replacements,
                )
                raise RuntimeError("Caller rolls back after successful replacement")
        assert connection.execute("SELECT * FROM files").fetchall() == files
        assert connection.execute("SELECT * FROM chunks").fetchall() == chunks
        assert len(full_text_search(connection, "deploys")) == 1
        assert full_text_search(connection, "replacement") == []


@pytest.mark.database
def test_concurrent_replacements_do_not_mix_chunks(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id = uuid4()
    barrier = Barrier(2)

    def replace(content: str) -> UUID:
        with db.connect(database_url) as connection:
            barrier.wait(timeout=10)
            return replace_file(
                connection,
                source_id=source_id,
                path="docs/deploy.md",
                blob_sha=content,
                language="markdown",
                chunks=[chunk(content), chunk(content)],
            )

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(replace, text) for text in ("first", "second")]
        ids = [future.result(timeout=30) for future in futures]
    assert ids[0] == ids[1]
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM files").fetchone() == (1,)
        rows = connection.execute(
            "SELECT content, blob_sha FROM chunks JOIN files ON files.id = file_id"
        ).fetchall()
        assert rows in [[("first", "first")] * 2, [("second", "second")] * 2]


@pytest.mark.database
def test_websearch_ranking_filtering_and_reconnect(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id, other_source = uuid4(), uuid4()
    with db.connect(database_url) as connection:
        _, intended_id = seed_chunk(connection, source_id=source_id)
        seed_chunk(
            connection,
            source_id=source_id,
            path="distractor.md",
            content="Deploys and " + "ordinary work " * 30 + "frozen Fridays",
        )
        seed_chunk(connection, source_id=other_source, content="Deploys frozen Fridays")
        rankings = {}
        for query in ('"deploys are frozen"', "deploys frozen Fridays"):
            matches = full_text_search(connection, query, source_id=source_id)
            assert matches[0].id == intended_id
            assert all(match.source_id == source_id for match in matches)
            assert len(matches) == (1 if query.startswith('"') else 2)
            rankings[query] = matches
        assert len(full_text_search(connection, "deploys frozen Fridays")) == 3
        assert len(full_text_search(connection, "deploys -ordinary")) == 2
        for query in ("parental leave", "", "the and", "'; DROP TABLE chunks; --"):
            assert full_text_search(connection, query) == []
    with db.connect(database_url) as connection:
        for query, matches in rankings.items():
            assert full_text_search(connection, query, source_id=source_id) == matches


@pytest.mark.database
def test_full_text_limit_and_deterministic_ties(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id = uuid4()
    with db.connect(database_url) as connection:
        for number in reversed(range(1, 56)):
            seed_chunk(connection, source_id=source_id, chunk_id=UUID(int=number))
        before = full_text_search(connection, "deploys")
        assert [match.id for match in before] == [UUID(int=i) for i in range(1, 51)]
        assert len({match.rank for match in before}) == 1
    with db.connect(database_url) as connection:
        assert full_text_search(connection, "deploys") == before


@pytest.mark.database
@pytest.mark.parametrize("previous_version", [3, 4])
def test_long_paths_survive_schema_upgrade(
    database_url: str, tmp_path: Path, previous_version: int
) -> None:
    for version, path in db.migration_files(db.MIGRATIONS):
        if version <= 3:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    if previous_version == 4:
        legacy = Path(__file__).parent / "fixtures/migrations/0004_chunks.sql"
        (tmp_path / legacy.name).write_bytes(legacy.read_bytes())
    db.run_migrations(database_url, tmp_path)
    long_path = (
        "/".join(
            hashlib.md5(str(i).encode(), usedforsecurity=False).hexdigest()
            for i in range(1, 91)
        )[:2658]
        + ".md"
    )
    source_id, file_id = uuid4(), uuid4()
    with db.connect(database_url) as connection:
        if previous_version == 3:
            connection.execute(
                "INSERT INTO files VALUES (%s, %s, %s, 'original', 'markdown')",
                (file_id, source_id, long_path),
            )
        else:
            seed_chunk(connection, source_id=source_id, model="\t")
            original_chunks = connection.execute("SELECT * FROM chunks").fetchall()
        original_files = connection.execute("SELECT * FROM files").fetchall()
        ledger = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    db.run_migrations(database_url)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT * FROM files").fetchall() == original_files
        assert (
            connection.execute(
                "SELECT version, applied_at FROM schema_migrations "
                "WHERE version <= %s ORDER BY version",
                (previous_version,),
            ).fetchall()
            == ledger
        )
        if previous_version == 4:
            assert (
                connection.execute("SELECT * FROM chunks").fetchall() == original_chunks
            )
            assert full_text_search(connection, "deploys")[0].embedding_model == "\t"
        else:
            assert (
                replace_file(
                    connection,
                    source_id=source_id,
                    path=long_path,
                    blob_sha="replacement",
                    language="markdown",
                    chunks=[chunk()],
                )
                == file_id
            )
        seed_chunk(connection, source_id=uuid4(), path=long_path)
        assert any(
            match.path == long_path for match in full_text_search(connection, "deploys")
        )
        for statement in (
            "UPDATE chunks SET path = 'mismatched.md'",
            "UPDATE chunks SET source_id = '00000000-0000-0000-0000-000000000000'",
        ):
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(statement)


@pytest.mark.database
def test_migration_preserves_files_and_creates_generated_index_once(
    database_url: str, tmp_path: Path
) -> None:
    for version, path in db.migration_files(db.MIGRATIONS):
        if version <= 3:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    db.run_migrations(database_url, tmp_path)
    file_id, source_id = uuid4(), uuid4()
    with db.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO files VALUES (%s, %s, 'existing.md', 'original', 'markdown')",
            (file_id, source_id),
        )
        original = connection.execute("SELECT * FROM files").fetchall()
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        before = connection.execute(
            "SELECT oid, relname FROM pg_class WHERE relnamespace = "
            "current_schema()::regnamespace ORDER BY oid"
        ).fetchall()
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT * FROM files").fetchall() == original
        assert (
            connection.execute(
                "SELECT oid, relname FROM pg_class WHERE relnamespace = "
                "current_schema()::regnamespace ORDER BY oid"
            ).fetchall()
            == before
        )
        assert connection.execute(
            "SELECT attname, attgenerated, atttypmod FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname IN ('tsv', 'embedding') "
            "ORDER BY attname"
        ).fetchall() == [("embedding", "", -1), ("tsv", "s", -1)]
        indexes = connection.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = 'chunks'"
        ).fetchall()
        assert sum("USING gin (tsv)" in row[0] for row in indexes) == 1
        assert all("hnsw" not in row[0] and "ivfflat" not in row[0] for row in indexes)
        _, chunk_id = seed_chunk(connection, source_id=source_id)
        seed_chunk(
            connection,
            source_id=source_id,
            path="different.md",
            embedding=(1.0, 0.0),
            model="fixture/different-dimensions",
        )
        assert len(full_text_search(connection, "deploys")) == 2
        connection.execute(
            "UPDATE chunks SET content = 'Lunar schedule' WHERE id = %s", (chunk_id,)
        )
        assert full_text_search(connection, "lunar")[0].id == chunk_id
        assert len(full_text_search(connection, "deploys")) == 1
        for statement in (
            "UPDATE chunks SET source_id = '00000000-0000-0000-0000-000000000000'",
            "UPDATE chunks SET path = 'wrong.md'",
            "UPDATE chunks SET file_id = '00000000-0000-0000-0000-000000000000'",
            "UPDATE chunks SET start_line = 0",
            "UPDATE chunks SET end_line = 1",
            "UPDATE chunks SET embedding_model = ' '",
        ):
            with pytest.raises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(statement)
