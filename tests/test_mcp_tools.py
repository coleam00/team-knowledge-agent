"""The four MCP tools over both transports against a really ingested handbook, compared
with the HTTP API and the shared services."""

import asyncio
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

from app import ask as ask_module
from app import main
from app.ask import UNKNOWN_ANSWER
from app.config import Settings
from app.models import AnswerChunk, Answerer, get_answerer
from app.sources import Source
from tests.conftest import local_event_loop
from tests.handbook import create_handbook_repo
from tests.mcp_support import child_session
from tests.test_http import ClientFactory, wait_for_health
from tests.test_mcp import PRODUCTION_TOOLS, http_session

pytestmark = pytest.mark.database

ROTATE = "rotate API keys"
FREEZE = "When are deploys frozen?"
PARENTAL = "What is the parental leave policy?"


@dataclass
class Handbook:
    client: TestClient
    source: Source
    database_url: str


@pytest.fixture
def handbook(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
    tmp_path: Path,
) -> Iterator[Handbook]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", "1")
    repository = create_handbook_repo(tmp_path)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        response = client.post("/api/sources", json={"url": str(repository)})
        assert response.status_code == 202
        source_id = UUID(response.json()["id"])
        deadline = time.monotonic() + 45
        while True:
            source = Source.model_validate(
                client.get(f"/api/sources/{source_id}").json()
            )
            assert source.status != "failed", source.error
            if source.status == "ready":
                break
            assert time.monotonic() < deadline, "Source did not finish indexing"
            time.sleep(0.05)
        yield Handbook(client, source, database_url)


def payload(result: CallToolResult) -> object:
    assert not result.is_error, result.content
    structured = result.structured_content
    assert structured is not None
    return structured.get("result", structured)


async def exercise(session: ClientSession, handbook: Handbook) -> None:
    http, sid = handbook.client, str(handbook.source.id)
    await session.initialize()
    tools = (await session.list_tools()).tools
    assert sorted(tool.name for tool in tools) == PRODUCTION_TOOLS
    declared = {
        tool.name: (
            set(tool.input_schema["properties"]),
            set(tool.input_schema.get("required", [])),
        )
        for tool in tools
    }
    assert declared == {
        "search": ({"query", "source", "limit"}, {"query"}),
        "ask": ({"question", "source"}, {"question"}),
        "fetch_source": (
            {"source", "path", "start_line", "end_line"},
            {"source", "path"},
        ),
        "list_sources": (set(), set()),
    }

    # Storage starts after the protocol; tools report it unavailable until then.
    while (await session.call_tool("list_sources", {})).is_error:
        await anyio.sleep(0.05)
    listed = http.get("/api/sources").json()
    assert [row["id"] for row in listed] == [sid]
    assert listed[0]["status"] == "ready" and listed[0]["indexed_commit"]
    assert payload(await session.call_tool("list_sources", {})) == listed

    expected = http.get(
        "/api/search", params={"q": ROTATE, "source_id": sid, "limit": "2"}
    ).json()
    assert len(expected) == 2
    for _ in range(3):
        found = await session.call_tool(
            "search", {"query": ROTATE, "source": sid, "limit": 2}
        )
        assert payload(found) == expected
    # Omitted optional parameters take the HTTP defaults.
    assert payload(await session.call_tool("search", {"query": ROTATE})) == (
        http.get("/api/search", params={"q": ROTATE}).json()
    )

    answer = http.post("/api/ask", json={"question": FREEZE, "source_ids": [sid]})
    assert answer.status_code == 200 and answer.json()["known"] is True
    for _ in range(2):
        asked = await session.call_tool("ask", {"question": FREEZE, "source": sid})
        assert payload(asked) == answer.json()
    unknown = payload(
        await session.call_tool("ask", {"question": PARENTAL, "source": sid})
    )
    assert unknown == {"known": False, "answer": UNKNOWN_ANSWER, "citations": []}

    cited = answer.json()["citations"][0]
    assert cited["path"] == "docs/deploy.md"
    start, end = cited["start_line"], cited["end_line"]
    fetched = payload(
        await session.call_tool(
            "fetch_source",
            {
                "source": sid,
                "path": cited["path"],
                "start_line": start,
                "end_line": end,
            },
        )
    )
    assert isinstance(fetched, dict)
    assert fetched["content"] == cited["snippet"]
    assert fetched["commit"] == cited["commit"] == handbook.source.indexed_commit
    assert (fetched["start_line"], fetched["end_line"]) == (start, end)
    assert (
        fetched
        == http.get(
            f"/api/sources/{sid}/file",
            params={"path": cited["path"], "start": start, "end": end},
        ).json()
    )

    # Failures are tool errors; the same session keeps serving afterwards.
    deploy = {"source": sid, "path": "docs/deploy.md"}
    failures: list[tuple[str, dict[str, object]]] = [
        ("fetch_source", {"source": sid, "path": "../secret.md"}),
        ("fetch_source", {"source": sid, "path": "/etc/passwd"}),
        ("fetch_source", {"source": sid, "path": "docs/removed-long-ago.md"}),
        ("fetch_source", {"source": str(uuid4()), "path": "docs/deploy.md"}),
        ("fetch_source", deploy | {"start_line": 0}),
        ("fetch_source", deploy | {"start_line": 5, "end_line": 2}),
        ("fetch_source", {"source": "not-a-uuid", "path": "docs/deploy.md"}),
        ("search", {"query": ""}),
        ("search", {"query": ROTATE, "limit": 0}),
        ("search", {"query": ROTATE, "limit": "many"}),
        ("search", {"query": ROTATE, "source": "not-a-uuid"}),
        ("search", {}),
        ("ask", {"question": "   "}),
        ("ask", {"question": FREEZE, "source": 7}),
        ("no_such_tool", {}),
    ]
    for name, arguments in failures:
        result = await session.call_tool(name, arguments)
        assert result.is_error, (name, arguments)
    missing = await session.call_tool(
        "fetch_source", {"source": sid, "path": "docs/removed-long-ago.md"}
    )
    text = missing.content[0]
    assert isinstance(text, TextContent) and "not found" in text.text.lower()
    assert payload(await session.call_tool("list_sources", {})) == listed


def test_http_transport_matches_http_api_and_skips_answerer_when_unknown(
    handbook: Handbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions: list[str] = []

    class Counting:
        def __init__(self, inner: Answerer) -> None:
            self.inner = inner

        def answer(self, question: str, chunks: Sequence[AnswerChunk]) -> str:
            questions.append(question)
            return self.inner.answer(question, chunks)

    def counting(settings: Settings) -> Answerer:
        return Counting(get_answerer(settings))

    monkeypatch.setattr(ask_module, "get_answerer", counting)

    async def check() -> None:
        with anyio.fail_after(60):
            async with http_session(main.create_app()) as session:
                await exercise(session, handbook)

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())
    assert questions and PARENTAL not in questions
    assert set(questions) == {FREEZE}


def test_stdio_transport_matches_http_api(handbook: Handbook) -> None:
    async def check() -> None:
        with anyio.fail_after(60):
            async with child_session(
                Path(os.environ["TKA_DATA_DIR"]),
                production=True,
                extra_env={
                    "DATABASE_URL": handbook.database_url,
                    "TKA_ALLOW_LOCAL_SOURCES": "1",
                },
            ) as session:
                await exercise(session, handbook)

    with asyncio.Runner(loop_factory=local_event_loop) as runner:
        runner.run(check())
