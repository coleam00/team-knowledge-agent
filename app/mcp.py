from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from app.config import Settings
from app.mcp_tools import register_services as register_tools
from app.runtime import AppRuntime

ServiceRegistration = Callable[[MCPServer[None], Callable[[], Settings]], None]


def register_production_services(
    server: MCPServer[None], get_settings: Callable[[], Settings]
) -> None:
    register_tools(server, get_settings)


def create_mcp_server(
    get_settings: Callable[[], Settings],
    *,
    register_services: ServiceRegistration = register_production_services,
) -> MCPServer[None]:
    server = MCPServer[None]("Team Knowledge Agent")
    register_services(server, get_settings)
    return server


async def run_stdio(
    *, register_services: ServiceRegistration = register_production_services
) -> None:
    runtime = AppRuntime.from_env()
    server = create_mcp_server(
        runtime.get_settings, register_services=register_services
    )
    async with runtime.run():
        await server.run_stdio_async()
