import asyncio
import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db import connect

SOCKET = socket.socket


def local_event_loop() -> asyncio.AbstractEventLoop:
    # Windows event loops need a loopback socket pair for their internal wakeup.
    with patch("socket.socket", SOCKET):
        return asyncio.new_event_loop()


@pytest.fixture(autouse=True)
def isolated_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    monkeypatch.setenv("TKA_MODELS", "fake")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TKA_CHAT_MODEL", raising=False)
    monkeypatch.delenv("TKA_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("TKA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FACTORY_RUNTIME_STATE", raising=False)
    monkeypatch.delenv("FACTORY_RUNTIME_CANDIDATE", raising=False)
    monkeypatch.delenv("TKA_BUILD_ID", raising=False)
    monkeypatch.delenv("TKA_ALLOW_LOCAL_SOURCES", raising=False)
    if request.node.get_closest_marker("database") is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)

        def forbidden_connection(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "Only database integration tests may connect to Postgres"
            )

        monkeypatch.setattr(psycopg, "connect", forbidden_connection)


@pytest.fixture
def client_factory() -> Callable[[FastAPI], TestClient]:
    def make_client(application: FastAPI) -> TestClient:
        return TestClient(
            application, backend_options={"loop_factory": local_event_loop}
        )

    return make_client


@pytest.fixture
def database_url() -> Iterator[str]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail(
            "Set DATABASE_URL to a supplied dedicated Postgres 17 database "
            "with pgvector available; integration tests never provision a server."
        )
    schema = f"tka_test_{uuid4().hex}"
    with connect(url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    options = conninfo_to_dict(url).get("options", "")
    try:
        yield make_conninfo(url, options=f"{options} -c search_path={schema},public")
    finally:
        with connect(url) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
