import re
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import TupleRow

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
MIGRATION_LOCK = 0x544B4101


def connect(database_url: str | None) -> psycopg.Connection[TupleRow]:
    if not database_url or not database_url.strip():
        raise ValueError("DATABASE_URL is required")
    options = conninfo_to_dict(database_url).get("options", "")
    return psycopg.connect(
        database_url,
        connect_timeout=3,
        options=f"{options} -c statement_timeout=5000 -c lock_timeout=5000",
    )


def migration_files(directory: Path) -> list[tuple[int, Path]]:
    migrations: dict[int, Path] = {}
    for path in directory.glob("*.sql"):
        match = re.fullmatch(r"([0-9]{4})_[a-z][a-z0-9_]*\.sql", path.name)
        if match is None:
            raise ValueError("Invalid migration filename")
        version = int(match[1])
        if version < 1 or version in migrations:
            raise ValueError("Invalid or duplicate migration version")
        migrations[version] = path
    if not migrations:
        raise ValueError("No migrations found")
    return sorted(migrations.items())


def run_migrations(database_url: str | None, directory: Path = MIGRATIONS) -> None:
    migrations = migration_files(directory)
    with connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        table = connection.execute(
            "SELECT to_regclass(format('%I.schema_migrations', current_schema()))"
        ).fetchone()
        applied = (
            {
                row[0]
                for row in connection.execute(
                    "SELECT version, applied_at FROM schema_migrations"
                )
            }
            if table is not None and table[0] is not None
            else set()
        )
        if applied - {version for version, _ in migrations}:
            raise ValueError("Database contains unknown migration versions")
        for version, path in migrations:
            if version not in applied:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )


def probe(database_url: str | None) -> None:
    with connect(database_url) as connection:
        if connection.execute("SELECT 1").fetchone() != (1,):
            raise RuntimeError("Database probe failed")
