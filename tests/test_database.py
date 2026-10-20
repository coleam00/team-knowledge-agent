from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

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
            "SELECT version, applied_at FROM schema_migrations"
        ).fetchall()
        assert len(before) == 1 and before[0][0] == 1 and before[0][1] is not None
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname='vector'"
        ).fetchone() == ("vector",)
    db.run_migrations(database_url)
    with db.connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT version, applied_at FROM schema_migrations"
            ).fetchall()
            == before
        )


@pytest.mark.database
def test_concurrent_initializations_converge(database_url: str) -> None:
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(db.run_migrations, database_url) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    with db.connect(database_url) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall() == [(1,)]


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
    for source in db.MIGRATIONS.glob("*.sql"):
        (tmp_path / source.name).write_text(source.read_text(), encoding="utf-8")
    (tmp_path / "0002_failure.sql").write_text(
        "CREATE TABLE must_rollback (id integer); SELECT 1 / 0;", encoding="utf-8"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        db.run_migrations(database_url, tmp_path)
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT to_regclass('must_rollback')").fetchone() == (
            None,
        )
        assert connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall() == [(1,)]


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
