from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from app import db


@pytest.mark.parametrize("url", [None, "", "   "])
def test_missing_database_never_uses_ambient_libpq_defaults(url: str | None) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        db.connect(url)


def test_extension_precedes_schema_and_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (None,)
    connection.__enter__.return_value = connection
    monkeypatch.setattr(db, "connect", lambda _: connection)
    db.run_migrations("test")
    statements = [call.args[0] for call in connection.execute.call_args_list]
    extension = statements.index(
        "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"
    )
    migration = next(i for i, sql in enumerate(statements) if "CREATE TABLE" in sql)
    ledger = next(i for i, sql in enumerate(statements) if "INSERT INTO" in sql)
    assert extension < migration < ledger


@pytest.mark.parametrize(
    "names",
    [[], ["bad.sql"], ["0000_invalid.sql"], ["0001_one.sql", "0001_duplicate.sql"]],
)
def test_invalid_migrations_fail_before_connecting(
    tmp_path: Path, names: list[str]
) -> None:
    for name in names:
        (tmp_path / name).write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(ValueError):
        db.run_migrations("unused", tmp_path)


@pytest.mark.database
def test_initialization_and_restart_keep_versions_and_timestamps(
    database_url: str,
) -> None:
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        before = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in before] == [
            version for version, _ in db.migration_files(db.MIGRATIONS)
        ]
        assert all(row[1] is not None for row in before)
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname='vector'"
        ).fetchone() == ("vector",)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT version, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
            == before
        )


@pytest.mark.database
def test_files_migration_upgrades_existing_source_registry(
    database_url: str, tmp_path: Path
) -> None:
    for name in ("0001_schema_migrations.sql", "0002_sources.sql"):
        (tmp_path / name).write_bytes((db.MIGRATIONS / name).read_bytes())
    db.run_migrations(database_url, tmp_path)
    source_id = uuid4()
    with db.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO sources (id, url, owner, name) VALUES (%s, %s, %s, %s)",
            (
                source_id,
                "https://github.com/tka-fixtures/handbook",
                "tka-fixtures",
                "handbook",
            ),
        )
        source = connection.execute("SELECT * FROM sources").fetchone()
        ledger = connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    db.run_migrations(database_url)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT * FROM sources").fetchone() == source
        assert (
            connection.execute(
                "SELECT version, applied_at FROM schema_migrations "
                "WHERE version <= 2 ORDER BY version"
            ).fetchall()
            == ledger
        )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        connection.execute(
            "INSERT INTO files (id, source_id, path, blob_sha, language) "
            "VALUES (%s, %s, %s, %s, %s)",
            (uuid4(), source_id, "README.md", "a" * 40, "markdown"),
        )
        assert connection.execute("SELECT source_id FROM files").fetchone() == (
            source_id,
        )


@pytest.mark.database
def test_files_persist_with_source_path_uniqueness(database_url: str) -> None:
    db.run_migrations(database_url)
    source_id, other_source = uuid4(), uuid4()
    records = [
        (uuid4(), source_id, "docs/caf\u00e9.md", "a" * 40, "markdown"),
        (uuid4(), other_source, "docs/caf\u00e9.md", "b" * 64, "markdown"),
        (uuid4(), source_id, "src/keys.py", "c" * 40, "python"),
    ]
    insert = (
        "INSERT INTO files (id, source_id, path, blob_sha, language) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    with db.connect(database_url) as connection:
        for record in records:
            connection.execute(insert, record)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert set(
            connection.execute(
                "SELECT id, source_id, path, blob_sha, language FROM files"
            ).fetchall()
        ) == set(records)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(insert, (uuid4(), *records[0][1:]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    insert,
                    (records[0][0], other_source, "new.md", "d" * 40, "markdown"),
                )
        for field in range(5):
            missing: list[object] = [uuid4(), uuid4(), "new.md", "d" * 40, "markdown"]
            missing[field] = None
            with pytest.raises(psycopg.errors.NotNullViolation):
                with connection.transaction():
                    connection.execute(insert, missing)


@pytest.mark.database
def test_concurrent_initializations_converge(database_url: str) -> None:
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(db.run_migrations, database_url) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    with db.connect(database_url) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version, _ in db.migration_files(db.MIGRATIONS)]


@pytest.mark.database
def test_unknown_version_fails_closed(database_url: str) -> None:
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        connection.execute("INSERT INTO schema_migrations (version) VALUES (9999)")
    with pytest.raises(ValueError, match="unknown migration"):
        db.run_migrations(database_url)


@pytest.mark.database
def test_incompatible_bookkeeping_fails_closed(database_url: str) -> None:
    with db.connect(database_url) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version integer PRIMARY KEY)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1)")
    with pytest.raises(psycopg.errors.UndefinedColumn):
        db.run_migrations(database_url)


@pytest.mark.database
def test_failed_forward_migration_rolls_back(database_url: str, tmp_path: Path) -> None:
    db.run_migrations(database_url)
    migrations = db.migration_files(db.MIGRATIONS)
    for _, source in migrations:
        (tmp_path / source.name).write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    next_version = migrations[-1][0] + 1
    (tmp_path / f"{next_version:04d}_failure.sql").write_text(
        "CREATE TABLE must_rollback (id integer); SELECT 1 / 0;", encoding="utf-8"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        db.run_migrations(database_url, tmp_path)
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT to_regclass('must_rollback')").fetchone() == (
            None,
        )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version, _ in migrations]


@pytest.mark.database
def test_failed_first_start_rolls_back_bookkeeping(
    database_url: str, tmp_path: Path
) -> None:
    (tmp_path / "0001_failure.sql").write_text(
        "CREATE TABLE schema_migrations (version integer PRIMARY KEY); SELECT 1 / 0;",
        encoding="utf-8",
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        db.run_migrations(database_url, tmp_path)
    with db.connect(database_url) as connection:
        assert connection.execute(
            "SELECT to_regclass(format('%I.schema_migrations', current_schema()))"
        ).fetchone() == (None,)
