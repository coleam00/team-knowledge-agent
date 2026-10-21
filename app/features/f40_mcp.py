from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from starlette.routing import Route

from app.config import Settings
from app.mcp import (
    ServiceRegistration,
    create_mcp_server,
    register_production_services,
)


def install(
    application: FastAPI,
    get_settings: Callable[[], Settings],
    *,
    register_services: ServiceRegistration = register_production_services,
) -> None:
    server = create_mcp_server(get_settings, register_services=register_services)
    http_application = server.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield

    application.include_router(
        APIRouter(
            routes=[Route("/mcp", endpoint=http_application, include_in_schema=False)],
            lifespan=lifespan,
        )
    )
