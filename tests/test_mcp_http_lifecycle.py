import asyncio
import socket
from collections.abc import Callable
from functools import partial

import anyio
import httpx2
import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from app import db, main
from app.config import Settings
from app.features import f40_mcp
from app.runtime import AppRuntime
from tests.conftest import local_event_loop
from tests.test_mcp import http_session


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    "protocol",
    [None, "2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"],
)
def test_disconnect_finishes_handler_while_server_stays_alive(
    monkeypatch: pytest.MonkeyPatch, protocol: str | None
) -> None:
    async def check() -> None:
        entered, finished, release = anyio.Event(), anyio.Event(), anyio.Event()

        def register(server: MCPServer[None], getter: Callable[[], Settings]) -> None:
            @server.tool()
            async def blocked() -> None:
                entered.set()
                try:
                    await release.wait()
                finally:
                    finished.set()

        monkeypatch.setattr(
            f40_mcp, "install", partial(f40_mcp.install, register_services=register)
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(
                uvicorn.Config(
                    main.create_app(), log_level="error", timeout_graceful_shutdown=1
                )
            )
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                with anyio.fail_after(10):
                    while not server.started:
                        await anyio.sleep(0.01)
                    async with httpx2.AsyncClient(base_url=url) as client:

                        async def call() -> None:
                            headers = {"Accept": "application/json, text/event-stream"}
                            params: dict[str, object] = {
                                "name": "blocked",
                                "arguments": {},
                            }
                            if protocol is not None:
                                headers["MCP-Protocol-Version"] = protocol
                            if protocol == "2026-07-28":
                                headers.update(
                                    {"MCP-Method": "tools/call", "MCP-Name": "blocked"}
                                )
                                params["_meta"] = {
                                    PROTOCOL_VERSION_META_KEY: protocol,
                                    CLIENT_CAPABILITIES_META_KEY: {},
                                }
                            response = await client.post(
                                "/mcp",
                                headers=headers,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/call",
                                    "params": params,
                                },
                            )
                            response.raise_for_status()

                        async with anyio.create_task_group() as calls:
                            calls.start_soon(call)
                            await entered.wait()
                            calls.cancel_scope.cancel()
                    with anyio.fail_after(2):
                        await finished.wait()
                    assert not server_task.done()
                    async with httpx2.AsyncClient(base_url=url) as client:
                        assert (await client.get("/api/build-id")).status_code == 200
            finally:
                release.set()
                server.should_exit = True
                await asyncio.wait_for(server_task, 5)
                assert server_task.done()

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())


def test_http_tool_observes_failed_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        initialized = anyio.Event()
        initialize = AppRuntime._initialize

        async def observe_initialization(runtime: AppRuntime) -> None:
            await initialize(runtime)
            initialized.set()

        def fail(database_url: str | None) -> None:
            raise ValueError("deliberate migration failure")

        def register(server: MCPServer[None], getter: Callable[[], Settings]) -> None:
            @server.tool()
            def storage_probe() -> str:
                return str(getter().data_dir)

        monkeypatch.setattr(db, "run_migrations", fail)
        monkeypatch.setattr(AppRuntime, "_initialize", observe_initialization)
        monkeypatch.setattr(
            f40_mcp, "install", partial(f40_mcp.install, register_services=register)
        )
        with anyio.fail_after(10):
            async with http_session(main.create_app()) as session:
                await initialized.wait()
                await session.initialize()
                result = await session.call_tool("storage_probe", {})
                assert result.is_error

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())
