"""The four MCP tools. Each calls the same service function as its HTTP route."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import anyio.to_thread
from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.ask import AskResult, AskUnavailable, ask
from app.config import Settings
from app.retrieval import Citation, InvalidSearch, SearchUnavailable, search
from app.source_files import SourceFileError, fetch_source_file
from app.sources import Source, list_sources

SourceId = Annotated[
    UUID | None,
    Field(description="Source id (as listed by list_sources) to restrict to"),
]


class SourceFileContent(BaseModel):
    source_id: UUID
    path: str
    commit: str
    start_line: int | None
    end_line: int | None
    total_lines: int
    content: str


def register_services(
    server: MCPServer[None], get_settings: Callable[[], Settings]
) -> None:
    def settings() -> Settings:
        try:
            return get_settings()
        except HTTPException as error:
            raise ToolError(str(error.detail)) from None

    @server.tool(name="search")
    async def search_tool(
        query: str, source: SourceId = None, limit: int = 10
    ) -> list[Citation]:
        """Search indexed sources and return ranked citations with snippets."""

        def run() -> list[Citation]:
            try:
                return search(settings(), query, source_id=source, limit=limit)
            except InvalidSearch:
                raise ToolError("Invalid search parameters") from None
            except SearchUnavailable:
                raise ToolError("Search is unavailable") from None

        return await anyio.to_thread.run_sync(run)

    @server.tool(name="ask")
    async def ask_tool(question: str, source: SourceId = None) -> AskResult:
        """Answer a question from indexed sources, citing the lines relied on."""

        def run() -> AskResult:
            try:
                return ask(
                    settings(),
                    question,
                    source_ids=None if source is None else {source},
                )
            except InvalidSearch:
                raise ToolError("Invalid ask parameters") from None
            except AskUnavailable:
                raise ToolError("Answers are unavailable") from None

        return await anyio.to_thread.run_sync(run)

    @server.tool(name="fetch_source")
    async def fetch_source_tool(
        source: UUID,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> SourceFileContent:
        """Read a file (or a 1-based inclusive line range) at the indexed commit."""

        def run() -> SourceFileContent:
            try:
                file = fetch_source_file(
                    source,
                    path=path,
                    start=start_line,
                    end=end_line,
                    settings=settings(),
                )
            except SourceFileError as error:
                raise ToolError(str(error)) from None
            return SourceFileContent(
                source_id=file.source_id,
                path=file.path,
                commit=file.commit,
                start_line=file.start_line,
                end_line=file.end_line,
                total_lines=file.total_lines,
                content=file.content,
            )

        return await anyio.to_thread.run_sync(run)

    @server.tool(name="list_sources")
    async def list_sources_tool() -> list[Source]:
        """List registered sources with status, indexed commit and counts."""

        def run() -> list[Source]:
            try:
                return list_sources(settings())
            except HTTPException as error:
                raise ToolError(str(error.detail)) from None

        return await anyio.to_thread.run_sync(run)
