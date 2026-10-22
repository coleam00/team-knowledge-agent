import os

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from scripts.smoke.core import Context
from tests.mcp_support import child_session


async def inspect_session(session: ClientSession) -> dict[str, object]:
    initialized = await session.initialize()
    tools = await session.list_tools()
    assert tools.tools == [], "Transport increment must expose no production tools"
    return {"protocol": initialized.protocol_version, "tools": []}


async def inspect_http(endpoint: str) -> dict[str, object]:
    with anyio.fail_after(15):
        async with streamable_http_client(endpoint) as streams:
            async with ClientSession(*streams) as session:
                result = await inspect_session(session)
    return result | {"endpoint": endpoint, "client_closed": True}


def check_http(ctx: Context, label: str) -> None:
    endpoint = str(ctx.client.base_url).rstrip("/") + "/mcp"
    result = anyio.run(inspect_http, endpoint)
    health = ctx.client.get("/api/health")
    identity = ctx.client.get("/api/build-id")
    assert health.status_code == identity.status_code == 200
    assert identity.text == os.environ["FACTORY_RUNTIME_CANDIDATE"]
    ctx.observations[label] = result | {"build_id": identity.text, "health": 200}


def before_restart(ctx: Context) -> None:
    check_http(ctx, "mcp_http")
    observations: dict[str, object] = {}

    async def inspect_stdio() -> None:
        with anyio.fail_after(20):
            async with child_session(
                ctx.state,
                production=True,
                extra_env={"DATABASE_URL": ctx.database_url},
                observations=observations,
            ) as session:
                observations.update(await inspect_session(session))

    anyio.run(inspect_stdio)
    ctx.observations["mcp_stdio"] = observations


def after_restart(ctx: Context) -> None:
    check_http(ctx, "mcp_http_after_restart")
