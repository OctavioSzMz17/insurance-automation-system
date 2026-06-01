"""SSE transport builder. Each MCP server uses this to expose tools over SSE (Rule 3).

Provides:
- /sse        — MCP SSE Mount (the only tool transport in production)
- /health     — open, returns {"status":"ok"} only (Rule 7: no env posture leak)
- /internal/ready — authenticated, real DB+Redis ping (Rule 7)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import db, redis_client
from .auth import MCPSecretAuthMiddleware
from .logging import configure as _configure_logging
from .logging import get_logger
from .settings import MCPSettings

_logger = get_logger(__name__)

ReadyCheck = Callable[[MCPSettings], Awaitable[None]]


def _build_health_route() -> Route:
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Route("/health", endpoint=health, methods=["GET"])


def _build_ready_route(settings: MCPSettings, extra_check: ReadyCheck | None) -> Route:
    async def ready(_: Request) -> JSONResponse:
        try:
            await db.ping(settings.database_url)
            if settings.redis_url:
                await redis_client.ping(settings.redis_url)
            if extra_check is not None:
                await extra_check(settings)
        except Exception:
            _logger.exception("ready check failed", extra={"event": "mcp.ready.fail"})
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return Route("/internal/ready", endpoint=ready, methods=["GET"])


def build_app(
    *,
    settings: MCPSettings,
    server: Server,
    ready_check: ReadyCheck | None = None,
) -> Starlette:
    """Compose the Starlette app: SSE + health + ready, secured by X-MCP-Secret."""
    _configure_logging(settings.log_level)

    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope, request.receive, request._send  # noqa: SLF001 — SDK contract
        ) as (read, write):
            await server.run(read, write, server.create_initialization_options())

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await db.get_engine(settings.database_url)
        if settings.redis_url:
            await redis_client.get_client(settings.redis_url)
        _logger.info(
            "mcp boot",
            extra={"event": "mcp.boot", "domain": settings.mcp_domain, "env": settings.environment},
        )
        try:
            yield
        finally:
            await db.dispose_engine()
            await redis_client.dispose_client()

    routes = [
        _build_health_route(),
        _build_ready_route(settings, ready_check),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(MCPSecretAuthMiddleware, shared_secret=settings.mcp_shared_secret)
    return app
