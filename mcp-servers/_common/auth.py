"""MCP transport authentication — shared by every MCP server.

Closes the CRITICAL gap identified in the post-Fix-1 audit:
    The Starlette/Uvicorn SSE servers accept any HTTP client that
    reaches the route. Without this middleware, an attacker on the
    docker network (or anyone bypassing ingress) can speak MCP
    directly and invoke create_claim / update_claim_status /
    append_reasoning, bypassing the orchestrator's JWT, RBAC, and
    the Rule 6 HITL gate entirely.

Design:
    - Pure-ASGI middleware (NOT BaseHTTPMiddleware) so SSE streaming
      responses pass through without buffering.
    - Validates `X-MCP-Secret` header with `hmac.compare_digest` —
      constant-time, immune to timing side-channels.
    - Fails closed: missing header, wrong length, wrong value → 403.
    - Fails fast at import time if MCP_SHARED_SECRET is unset or
      shorter than 32 bytes (generated via `openssl rand -hex 32`).

Wire-up (in every MCP server's server.py):

    from starlette.middleware import Middleware
    sys.path.insert(0, str(_HERE.parent))      # mcp-servers/
    from _common.auth import MCPAuthMiddleware

    starlette_app = Starlette(
        middleware=[Middleware(MCPAuthMiddleware)],
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse_transport.handle_post_message),
        ],
    )

Rules applied:
    Rule 7  — All MCP traffic now authenticated at the transport layer
    Rule 8  — Secret loaded from .env, never hardcoded
    Rule 9  — Structured logging on every auth denial
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Load the project-root .env BEFORE reading MCP_SHARED_SECRET. Each MCP
# server also loads .env via its own db.py, but this module may be
# imported before db.py — make it self-sufficient.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# Minimum entropy for the shared secret. 32 bytes (= 64 hex chars from
# `openssl rand -hex 32`) gives 256 bits — well above any brute-force
# threshold even against an attacker with local network access.
_MIN_SECRET_BYTES = 32

_secret_str = os.environ.get("MCP_SHARED_SECRET", "")
if not _secret_str or len(_secret_str) < _MIN_SECRET_BYTES:
    raise RuntimeError(
        "MCP_SHARED_SECRET must be set in .env and at least "
        f"{_MIN_SECRET_BYTES} characters long. "
        "Generate one with: openssl rand -hex 32"
    )

_SECRET: bytes = _secret_str.encode("utf-8")
logger.info(
    "mcp auth middleware armed | secret_len=%d source=MCP_SHARED_SECRET",
    len(_SECRET),
)


class MCPAuthMiddleware:
    """Pure-ASGI middleware that rejects any HTTP request without a valid
    `X-MCP-Secret` header.

    Runs BEFORE the Starlette router resolves the request, so /sse and
    /messages/ are protected uniformly. Non-HTTP scopes (lifespan,
    websocket) pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # ASGI headers are list[tuple[bytes, bytes]]. Header names are
        # always lowercase per the ASGI spec.
        presented = b""
        for name, value in scope.get("headers", []):
            if name == b"x-mcp-secret":
                presented = value
                break

        if not hmac.compare_digest(presented, _SECRET):
            client = scope.get("client") or ("?", 0)
            path = scope.get("path", "?")
            reason = "missing_header" if not presented else "invalid_secret"
            logger.warning(
                "mcp auth DENIED | remote=%s path=%s reason=%s",
                client[0], path, reason,
            )
            response = PlainTextResponse(
                "forbidden: missing or invalid X-MCP-Secret header",
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
