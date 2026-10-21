import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer

from app import db, main
from app.config import Settings
from app.features import f40_mcp
from app.mcp import create_mcp_server, run_stdio
from tests.conftest import local_event_loop
from tests.mcp_support import (
    ROOT,
    child_environment,
    child_session,
    register_test_services,
)


@asynccontextmanager
async def http_session(application: FastAPI) -> AsyncIterator[ClientSession]:
    async with application.router.lifespan_context(application):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=application),
            base_url="http://127.0.0.1:8765",
        ) as client:
            async with streamable_http_client(
                "http://127.0.0.1:8765/mcp", http_client=client
            ) as streams:
                async with ClientSession(*streams) as session:
                    yield session


async def probe(session: ClientSession) -> tuple[dict[str, object], dict[str, object]]:
    await session.initialize()
    tools = await session.list_tools()
    assert [tool.name for tool in tools.tools] == ["transport_probe"]
    invalid = await session.call_tool("transport_probe", {"text": {"invalid": True}})
    assert invalid.is_error
    result = await session.call_tool("transport_probe", {"text": "déploiement 日本語"})
    assert not result.is_error
    assert result.structured_content == {
        "text": "déploiement 日本語",
        "nested": {"values": [1, 2, 3]},
    }
    return tools.model_dump(mode="json"), result.model_dump(mode="json")


def test_transport_parity_and_registration_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registrar = Mock(side_effect=register_test_services)
    original = f40_mcp.install
    monkeypatch.setattr(
        f40_mcp, "install", partial(original, register_services=registrar)
    )
    application = main.create_app()
    registrar.assert_called_once()
    monkeypatch.setattr(f40_mcp, "install", original)
    production = main.create_app()

    async def check() -> None:
        with anyio.fail_after(30):
            async with http_session(application) as session:
                http_result = await probe(session)
            async with child_session(tmp_path) as session:
                assert await probe(session) == http_result
            async with http_session(production) as session:
                await session.initialize()
                assert (await session.list_tools()).tools == []
                assert (await session.call_tool("transport_probe", {})).is_error
            async with child_session(tmp_path, production=True) as session:
                await session.initialize()
                assert (await session.list_tools()).tools == []
                assert (await session.call_tool("transport_probe", {})).is_error

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_registrar_failure_aborts_construction() -> None:
    registrar = Mock(side_effect=ValueError("invalid registration"))
    with pytest.raises(ValueError, match="invalid registration"):
        create_mcp_server(Mock(), register_services=registrar)
    registrar.assert_called_once()


@pytest.mark.parametrize("failed", [False, True])
def test_protocol_is_available_during_storage_startup(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    entered, release = threading.Event(), threading.Event()

    def migrate(_: str | None) -> None:
        entered.set()
        assert release.wait(10)
        if failed:
            raise RuntimeError("secret")

    monkeypatch.setattr(db, "run_migrations", migrate)
    monkeypatch.setattr(db, "probe", Mock())
    application = main.create_app()

    async def check() -> None:
        async with http_session(application) as session:
            try:
                assert await anyio.to_thread.run_sync(entered.wait, 5)
                await session.initialize()
                assert (await session.list_tools()).tools == []
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=application),
                    base_url="http://127.0.0.1:8765",
                ) as client:
                    assert (await client.get("/api/build-id")).status_code == 200
                    for path in ["/api/health", "/api/sources", "/api/search?q=x"]:
                        assert (await client.get(path)).status_code == 503
            finally:
                release.set()

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://127.0.0.1:8765"])
def test_exact_path_and_host_origin_protection(origin: str) -> None:
    application = main.create_app()

    async def check() -> None:
        async with application.router.lifespan_context(application):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=application),
                base_url=origin,
            ) as client:
                message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
                headers = {
                    "Accept": "application/json, text/event-stream",
                    "Origin": origin,
                }
                response = await client.post("/mcp", json=message, headers=headers)
                assert response.status_code == 200 and not response.history
                assert response.json()["result"]["serverInfo"]["name"]
                for extra in [
                    {"Host": "attacker.invalid"},
                    {"Origin": "https://attacker.invalid"},
                ]:
                    rejected = await client.post(
                        "/mcp", json=message, headers=headers | extra
                    )
                    assert rejected.status_code in {403, 421}

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_stdio_raw_output_and_eof(tmp_path: Path) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "raw", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]

    async def check() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app",
            "mcp",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_environment(tmp_path),
            cwd=ROOT,
        )
        try:
            with anyio.fail_after(15):
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write((json.dumps(messages[0]) + "\n").encode())
                await process.stdin.drain()
                initialized = await process.stdout.readline()
                assert json.loads(initialized)["id"] == 1
                for message in messages[1:]:
                    process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()
                listed = await process.stdout.readline()
                assert json.loads(listed)["result"]["tools"] == []
                process.stdin.close()
                trailing, diagnostics = await process.communicate()
                assert process.returncode == 0
                assert trailing == b""
                lines = [json.loads(line) for line in [initialized, listed]]
                assert all(line["jsonrpc"] == "2.0" for line in lines)
                assert [line["id"] for line in lines] == [1, 2]
                assert b"Initialization failed (ValueError)" in diagnostics
        finally:
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), 5)

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_stdio_invalid_configuration_precedes_writes(tmp_path: Path) -> None:
    data = tmp_path / "must-not-exist"
    environment = child_environment(data) | {"TKA_MODELS": "real"}
    result = subprocess.run(
        [sys.executable, "-m", "app", "mcp"],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
        cwd=ROOT,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "OPENROUTER_API_KEY is required" in result.stderr
    assert not data.exists()


def test_http_handler_cancellation_runs_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        entered, finished = anyio.Event(), anyio.Event()

        def register(server: MCPServer[None], getter: Callable[[], Settings]) -> None:
            @server.tool()
            async def blocked() -> None:
                entered.set()
                try:
                    await anyio.sleep_forever()
                finally:
                    finished.set()

        monkeypatch.setattr(
            f40_mcp, "install", partial(f40_mcp.install, register_services=register)
        )
        with anyio.fail_after(10):
            async with http_session(main.create_app()) as session:
                await session.initialize()
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(session.call_tool, "blocked", {})
                    await entered.wait()
                    tasks.cancel_scope.cancel()
            await finished.wait()

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_stdio_client_cancellation_finishes_handler_and_reaps_child(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        with anyio.fail_after(15):
            with anyio.CancelScope() as scope:
                async with child_session(
                    tmp_path, extra_env={"TKA_TEST_MCP_LIFECYCLE": "1"}
                ) as session:
                    await session.initialize()
                    async with anyio.create_task_group() as tasks:
                        tasks.start_soon(session.call_tool, "blocked", {})
                        while not (tmp_path / "entered").exists():
                            await anyio.sleep(0.01)
                        scope.cancel()
            assert (tmp_path / "finished").exists()

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_stdio_registers_once_and_owns_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    registrar = Mock(side_effect=register_test_services)
    migration = Mock()
    monkeypatch.setattr(db, "run_migrations", migration)
    monkeypatch.setattr(main, "create_app", Mock(side_effect=AssertionError))

    async def serve(server: MCPServer[None]) -> None:
        with anyio.fail_after(5):
            while not migration.called:
                await anyio.sleep(0.01)

    monkeypatch.setattr(MCPServer, "run_stdio_async", serve)
    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(run_stdio(register_services=registrar))
    registrar.assert_called_once()
    migration.assert_called_once()


def test_stdio_eof_joins_pending_initialization(tmp_path: Path) -> None:
    async def check() -> None:
        closing, closed = anyio.Event(), anyio.Event()

        async def client() -> None:
            async with child_session(
                tmp_path, extra_env={"TKA_TEST_MCP_BLOCK_INITIALIZATION": "1"}
            ) as session:
                await session.initialize()
                while not (tmp_path / "initializing").exists():
                    await anyio.sleep(0.01)
                closing.set()
            closed.set()

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(client)
                await closing.wait()
                try:
                    with anyio.move_on_after(2.5) as timeout:
                        await closed.wait()
                    assert timeout.cancelled_caught
                    assert not (tmp_path / "migration_closed").exists()
                finally:
                    (tmp_path / "release").touch()
                await closed.wait()
            assert (tmp_path / "migration_closed").exists()

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())
