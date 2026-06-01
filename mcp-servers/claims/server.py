"""Claims MCP server entrypoint.

Run with:  uvicorn claims.server:app --host 0.0.0.0 --port 8001
PYTHONPATH must contain the monorepo root and `mcp-servers/` so `_common.*` and
`packages.common.*` resolve (Dockerfile/Compose set this; tests do it in conftest).
"""
from __future__ import annotations

from mcp.server import Server

from _common.server_wiring import register_tools
from _common.settings import MCPSettings
from _common.transport import build_app

from .tools import build_tools


def _load_settings() -> MCPSettings:
    # mcp_domain is the only field we hardcode per-MCP; everything else flows from env.
    return MCPSettings(mcp_domain="claims")  # type: ignore[call-arg]


def create_app():  # noqa: ANN201 — Starlette app
    settings = _load_settings()
    server = Server("claims")
    register_tools(server, build_tools(settings))
    return build_app(settings=settings, server=server)


app = create_app()
