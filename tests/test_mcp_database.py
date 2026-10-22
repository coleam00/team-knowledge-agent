import asyncio
from pathlib import Path
from uuid import uuid4

import anyio
import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import TupleRow

from app import db, main
from scripts.smoke.core import ledger
from tests.conftest import local_event_loop
from tests.mcp_support import child_session
from tests.test_mcp import http_session


@pytest.mark.database
def test_transports_share_migrations_and_close_connections(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    application_name = "mcp_test_" + uuid4().hex
    url = make_conninfo(database_url, application_name=application_name)
    monkeypatch.setenv("DATABASE_URL", url)
    connections: list[psycopg.Connection[TupleRow]] = []
    connect = db.connect
    migrated = 0
    migrate = db.run_migrations

    def observe_connection(database_url: str | None) -> psycopg.Connection[TupleRow]:
        connection = connect(database_url)
        connections.append(connection)
        return connection

    def observe_migration(database_url: str | None) -> None:
        nonlocal migrated
        migrate(database_url)
        migrated += 1

    monkeypatch.setattr(db, "connect", observe_connection)
    monkeypatch.setattr(db, "run_migrations", observe_migration)

    async def check() -> None:
        with anyio.fail_after(20):
            async with http_session(main.create_app()) as session:
                await session.initialize()
                assert (await session.list_tools()).tools == []
                while migrated == 0:
                    await anyio.sleep(0.01)
                assert migrated == 1
            assert connections and all(connection.closed for connection in connections)
            before = ledger(url)
            async with child_session(
                tmp_path,
                extra_env={"DATABASE_URL": url, "TKA_TEST_MCP_LIFECYCLE": "1"},
            ) as session:
                await session.initialize()
                while True:
                    ready = await session.call_tool("storage_ready", {})
                    assert not ready.is_error
                    if ready.structured_content == {"result": True}:
                        break
                    await anyio.sleep(0.01)
            assert ledger(url) == before
            with connect(database_url) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
                    (application_name,),
                ).fetchone() == (0,)

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())
