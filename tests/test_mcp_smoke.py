import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest

from scripts.smoke.features import f40_mcp
from tests.conftest import local_event_loop


def test_failed_mcp_exchange_fails_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def failed(endpoint: str) -> AsyncIterator[None]:
        raise httpx2.ConnectError("unavailable")
        yield

    monkeypatch.setattr(f40_mcp, "streamable_http_client", failed)
    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        with pytest.raises(httpx2.ConnectError):
            runner.run(f40_mcp.inspect_http("http://127.0.0.1:8765/mcp"))
