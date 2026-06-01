"""Users MCP server entrypoint."""
from __future__ import annotations

from mcp.server import Server

from _common.server_wiring import register_tools
from _common.settings import MCPSettings
from _common.transport import build_app

from .tools import build_tools


def _load_settings() -> MCPSettings:
    return MCPSettings(mcp_domain="users")  # type: ignore[call-arg]


def create_app():  # noqa: ANN201
    settings = _load_settings()
    server = Server("users")
    register_tools(server, build_tools(settings))
    return build_app(settings=settings, server=server)


app = create_app()
