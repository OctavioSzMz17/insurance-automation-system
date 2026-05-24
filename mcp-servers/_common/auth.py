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

import asyncio
import hmac
import logging
import os
import time
from collections import defaultdict, deque
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

# Fix #85 — per-IP rate limiting on authenticated MCP requests.
# Applied AFTER the X-MCP-Secret check so unauthenticated probes don't
# consume quota. Values are intentionally generous (60 req/min) since the
# only legitimate caller is the orchestrator; anything higher is anomalous.
_RATE_LIMIT_MAX    = int(os.environ.get("MCP_RATE_LIMIT_MAX",    "60"))
_RATE_LIMIT_WINDOW = int(os.environ.get("MCP_RATE_LIMIT_WINDOW", "60"))  # seconds

_secret_str = os.environ.get("MCP_SHARED_SECRET", "")
if not _secret_str or len(_secret_str) < _MIN_SECRET_BYTES:
    raise RuntimeError(
        "MCP_SHARED_SECRET must be set in .env and at least "
        f"{_MIN_SECRET_BYTES} characters long. "
        "Generate one with: openssl rand -hex 32"
    )

# Boot-time cache — validated above, used as a safe fallback by _get_secret().
_SECRET: bytes = _secret_str.encode("utf-8")
logger.info(
    "mcp auth middleware armed | secret_len=%d source=MCP_SHARED_SECRET",
    len(_SECRET),
)


def _get_secret() -> bytes:
    """Return the current MCP_SHARED_SECRET as bytes.

    Fix #90 — re-reads the environment variable on every call so the secret
    can be rotated at runtime (e.g. via a K8s volume-mounted Secret update)
    without restarting the MCP server.

    Fallback: if the env var has been removed or shortened below the minimum
    after startup (should never happen in a well-operated cluster), the
    boot-time validated value is returned and a WARNING is emitted.
    The server does NOT crash mid-request — failing closed on a bad rotation
    would lock out the orchestrator.
    """
    raw = os.environ.get("MCP_SHARED_SECRET", "")
    if raw and len(raw) >= _MIN_SECRET_BYTES:
        return raw.encode("utf-8")
    # Degraded state — log prominently and fall back to boot-time value.
    logger.warning(
        "mcp auth | MCP_SHARED_SECRET missing/short in env during request "
        "— using boot-time value; rotate and redeploy as soon as possible"
    )
    return _SECRET


class MCPAuthMiddleware:
    """Pure-ASGI middleware that rejects any HTTP request without a valid
    `X-MCP-Secret` header, then enforces a per-IP sliding-window rate limit.

    Runs BEFORE the Starlette router resolves the request, so /sse and
    /messages/ are protected uniformly. Non-HTTP scopes (lifespan,
    websocket) pass through untouched.

    Fix #85 — rate limiting is applied only on AUTHENTICATED requests so
    unauthenticated probes cannot exhaust the quota of legitimate callers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Fix #85 — per-IP sliding-window state.
        # asyncio.Lock is safe to create in __init__ from Python 3.10+
        # (no running event loop required).
        self._rate_windows: dict[str, deque[float]] = defaultdict(deque)
        self._rate_lock: asyncio.Lock = asyncio.Lock()

    async def _check_rate_limit(self, client_ip: str) -> bool:
        """Return True if the request is allowed, False if rate-limited.

        Uses a sliding window: entries older than _RATE_LIMIT_WINDOW seconds
        are evicted before the count is checked. Amortises cleanup cost to O(k)
        where k is the number of expired entries — bounded by _RATE_LIMIT_MAX.

        Fix #85 — in-memory per-process store (no Redis dependency at the MCP
        layer). Sufficient because the only legitimate caller is the single
        orchestrator; an attacker with a leaked secret would be blocked here.
        """
        async with self._rate_lock:
            now    = time.monotonic()
            cutoff = now - _RATE_LIMIT_WINDOW
            dq     = self._rate_windows[client_ip]

            # Evict timestamps outside the current window (oldest first).
            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= _RATE_LIMIT_MAX:
                return False  # over quota

            dq.append(now)
            return True

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

        if not hmac.compare_digest(presented, _get_secret()):
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

        # Fix #85 — rate limit check (after auth so only valid callers are counted).
        client_ip = (scope.get("client") or ("unknown", 0))[0]
        if not await self._check_rate_limit(client_ip):
            logger.warning(
                "mcp rate-limit BLOCKED | remote=%s limit=%d/%ds",
                client_ip, _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW,
            )
            response = PlainTextResponse(
                f"rate limit exceeded: max {_RATE_LIMIT_MAX} requests "
                f"per {_RATE_LIMIT_WINDOW}s per IP",
                status_code=429,
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
