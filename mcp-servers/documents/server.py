"""Documents MCP server entrypoint."""
from __future__ import annotations

from mcp.server import Server

from _common.server_wiring import register_tools
from _common.settings import MCPSettings
from _common.transport import build_app

from .tools import build_tools


def _load_settings() -> MCPSettings:
    return MCPSettings(mcp_domain="documents")  # type: ignore[call-arg]


def create_app():  # noqa: ANN201
    settings = _load_settings()
    settings.validate_upload_dir()  # fail-fast at boot if /tmp in prod, missing dir, etc.
    server = Server("documents")
    register_tools(server, build_tools(settings))
    return build_app(settings=settings, server=server)


app = create_app()
