"""X-MCP-Secret transport auth middleware (local-dev acceptable, mTLS is the prod target — Rule 7).

Constant-time compare to avoid leaking secret length via timing side channel.
"""
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .logging import get_logger

_logger = get_logger(__name__)

_OPEN_PATHS: frozenset[str] = frozenset({"/health"})


class MCPSecretAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, shared_secret: str) -> None:
        super().__init__(app)
        if not shared_secret:
            raise RuntimeError("MCPSecretAuthMiddleware requires a non-empty shared_secret")
        self._secret = shared_secret

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)
        presented = request.headers.get("x-mcp-secret", "")
        if not presented or not hmac.compare_digest(presented, self._secret):
            _logger.warning(
                "mcp auth rejected",
                extra={"event": "mcp.auth.reject", "path": request.url.path},
            )
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response: Response = await call_next(request)
        return response
