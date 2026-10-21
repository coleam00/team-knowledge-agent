import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO
from unittest.mock import patch

import anyio
import mcp.client.stdio as stdio
import pytest_socket
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters
from mcp.os.win32.utilities import ServerProcess
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from app import db
from app.config import Settings
from app.mcp import run_stdio
from tests.conftest import local_event_loop

ROOT = Path(__file__).resolve().parents[1]


class ProbeResult(BaseModel):
    text: str
    nested: dict[str, list[int]]


def register_test_services(
    server: MCPServer[None], get_settings: Callable[[], Settings]
) -> None:
    @server.tool()
    def transport_probe(text: str) -> ProbeResult:
        return ProbeResult(text=text, nested={"values": [1, 2, 3]})


def register_lifecycle_services(
    server: MCPServer[None], get_settings: Callable[[], Settings]
) -> None:
    @server.tool()
    def storage_ready() -> bool:
        try:
            get_settings()
        except HTTPException:
            return False
        return True

    @server.tool()
    async def blocked() -> None:
        directory = Path(os.environ["TKA_DATA_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "entered").touch()
        try:
            await anyio.sleep_forever()
        finally:
            (directory / "finished").touch()


def child_environment(data_dir: Path) -> dict[str, str]:
    return {
        **stdio.get_default_environment(),
        "TKA_MODELS": "fake",
        "TKA_DATA_DIR": str(data_dir),
        "PYTHONIOENCODING": "utf-8",
    }


def wait_for_storage_release(database_url: str | None) -> None:
    directory = Path(os.environ["TKA_DATA_DIR"])
    (directory / "initializing").touch()
    try:
        deadline = time.monotonic() + 10
        while not (directory / "release").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Test did not release initialization")
            time.sleep(0.01)
    finally:
        (directory / "migration_closed").touch()


@asynccontextmanager
async def child_session(
    data_dir: Path,
    *,
    production: bool = False,
    extra_env: dict[str, str] | None = None,
    observations: dict[str, object] | None = None,
) -> AsyncIterator[ClientSession]:
    processes: list[ServerProcess] = []
    original = stdio._create_platform_compatible_process

    async def capture(
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        errlog: TextIO = sys.stderr,
        cwd: Path | str | None = None,
    ) -> ServerProcess:
        process = await original(command, args, env, errlog, cwd)
        processes.append(process)
        if observations is not None:
            observations.update(pid=process.pid, argv=[command, *args])
        return process

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app", "mcp"] if production else ["-m", "tests.mcp_support"],
        env=child_environment(data_dir) | (extra_env or {}),
        cwd=ROOT,
    )
    try:
        # EOF waits for initialization, whose database timeouts exceed the SDK's 2s.
        with (
            patch.object(stdio, "_create_platform_compatible_process", capture),
            patch.object(stdio, "PROCESS_TERMINATION_TIMEOUT", 10.0),
        ):
            async with stdio.stdio_client(parameters) as streams:
                async with ClientSession(*streams) as session:
                    yield session
    finally:
        assert len(processes) == 1
        if observations is not None:
            observations.update(
                exit_code=processes[0].returncode,
                cleanup_confirmed=processes[0].returncode is not None,
            )
        assert processes[0].returncode == 0, "Child did not exit naturally on EOF"


if __name__ == "__main__":
    pytest_socket.disable_socket(allow_unix_socket=True)
    with (
        patch.object(
            db,
            "run_migrations",
            wait_for_storage_release
            if os.environ.get("TKA_TEST_MCP_BLOCK_INITIALIZATION") == "1"
            else db.run_migrations,
        ),
        asyncio.Runner(loop_factory=local_event_loop) as runner,
    ):
        runner.run(
            run_stdio(
                register_services=register_lifecycle_services
                if os.environ.get("TKA_TEST_MCP_LIFECYCLE") == "1"
                else register_test_services
            )
        )
