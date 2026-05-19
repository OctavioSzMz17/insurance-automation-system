"""FastAPI middleware that binds a per-request trace_id — Phase D2.4.

Pipeline:

    1. Request hits the middleware.
    2. If the caller sent `X-Trace-Id`, we honour it (lets a frontend
       or upstream service correlate its own logs with the
       orchestrator's). Otherwise we mint a fresh UUID.
    3. We bind the id to the request-scoped `ContextVar` so any
       `get_trace_id()` call downstream returns the same value.
    4. After the handler runs, we echo the id back in the response so
       the client can pin it to whatever they're debugging.

This is the single place where the trace_id is created or imported.
Everything else (logs, MCP calls, Langfuse sessions) just reads it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Make project-root imports work when this module is loaded directly.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from observability.correlation import (  # noqa: E402
    new_trace_id,
    set_trace_id,
)

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Binds X-Trace-Id (or a fresh UUID) to the request's context."""

    def __init__(self, app: ASGIApp, header: str = TRACE_HEADER) -> None:
        super().__init__(app)
        self._header = header

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self._header)
        trace_id = incoming or new_trace_id()
        set_trace_id(trace_id)

        try:
            response: Response = await call_next(request)
        except Exception:
            # Surface the trace_id in any stack/log emitted by the
            # exception handler too, then let it propagate.
            logger.exception(
                "Unhandled exception during request",
                extra={"trace_id": trace_id},
            )
            raise

        # Echo the id back so the caller can pin it.
        response.headers[self._header] = trace_id
        return response
