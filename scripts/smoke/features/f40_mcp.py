import os

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from app.sources import Source
from scripts.smoke.core import Context
from tests.mcp_support import child_session

TOOLS = ["ask", "fetch_source", "list_sources", "search"]


def unwrap(result: CallToolResult) -> object:
    assert not result.is_error, result.content
    structured = result.structured_content
    assert structured is not None
    return structured.get("result", structured)


async def inspect_session(
    session: ClientSession, ctx: Context | None = None
) -> dict[str, object]:
    initialized = await session.initialize()
    tools = await session.list_tools()
    assert sorted(tool.name for tool in tools.tools) == TOOLS
    observed: dict[str, object] = {
        "protocol": initialized.protocol_version,
        "tools": TOOLS,
    }
    source = None if ctx is None else ctx.data.get("ask_source")
    if ctx is None or source is None:
        return observed
    assert isinstance(source, Source)
    sid, http = str(source.id), ctx.client
    while (await session.call_tool("list_sources", {})).is_error:
        await anyio.sleep(0.05)
    listed = http.get("/api/sources").json()
    assert unwrap(await session.call_tool("list_sources", {})) == listed
    params = {"q": "rotate API keys", "source_id": sid, "limit": "2"}
    expected = http.get("/api/search", params=params).json()
    assert 0 < len(expected) <= 2
    assert (
        unwrap(
            await session.call_tool(
                "search", {"query": "rotate API keys", "source": sid, "limit": 2}
            )
        )
        == expected
    )
    freeze = "When are deploys frozen?"
    answer = http.post("/api/ask", json={"question": freeze, "source_ids": [sid]})
    assert (
        unwrap(await session.call_tool("ask", {"question": freeze, "source": sid}))
        == answer.json()
    )
    unknown = await session.call_tool(
        "ask", {"question": "What is the parental leave policy?", "source": sid}
    )
    unknown_result = unwrap(unknown)
    assert isinstance(unknown_result, dict)
    assert unknown_result["known"] is False and unknown_result["citations"] == []
    cited = answer.json()["citations"][0]
    fetched = unwrap(
        await session.call_tool(
            "fetch_source",
            {
                "source": sid,
                "path": cited["path"],
                "start_line": cited["start_line"],
                "end_line": cited["end_line"],
            },
        )
    )
    assert isinstance(fetched, dict) and fetched["content"] == cited["snippet"]
    for path in ("../secret", "docs/absent-old-file.md"):
        missing = await session.call_tool("fetch_source", {"source": sid, "path": path})
        assert missing.is_error
    bad = await session.call_tool("search", {"query": "x", "limit": "many"})
    assert bad.is_error
    assert unwrap(await session.call_tool("list_sources", {})) == listed
    return observed | {"parity_checked": True, "fetched_lines": cited["snippet"]}


async def inspect_http(endpoint: str, ctx: Context | None = None) -> dict[str, object]:
    with anyio.fail_after(15):
        async with streamable_http_client(endpoint) as streams:
            async with ClientSession(*streams) as session:
                result = await inspect_session(session, ctx)
    return result | {"endpoint": endpoint, "client_closed": True}


def check_http(ctx: Context, label: str) -> None:
    endpoint = str(ctx.client.base_url).rstrip("/") + "/mcp"
    result = anyio.run(inspect_http, endpoint, ctx)
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
                extra_env={
                    "DATABASE_URL": ctx.database_url,
                    "TKA_ALLOW_LOCAL_SOURCES": "1",
                },
                observations=observations,
            ) as session:
                observations.update(await inspect_session(session, ctx))

    anyio.run(inspect_stdio)
    ctx.observations["mcp_stdio"] = observations


def after_restart(ctx: Context) -> None:
    check_http(ctx, "mcp_http_after_restart")
