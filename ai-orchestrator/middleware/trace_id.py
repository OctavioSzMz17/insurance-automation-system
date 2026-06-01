"""Per-request trace ID middleware.

Reads X-Trace-Id from incoming requests (or mints a fresh UUID4), binds
it to a ContextVar so logs can include it, then echoes it back in the
response header. Only safe characters (Fix #86) are accepted from callers.
"""
from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"

# Accept UUID-safe chars only — reject anything with CR/LF or ANSI escapes.
_SAFE_TRACE_RE = re.compile(r"^[A-Za-z0-9\-_]{1,128}$")

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    return _trace_id_var.get()


def new_trace_id() -> str:
    return str(uuid.uuid4())


class TraceIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, header: str = TRACE_HEADER) -> None:
        super().__init__(app)
        self._header = header

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(self._header)
        if incoming and _SAFE_TRACE_RE.match(incoming):
            trace_id = incoming
        else:
            if incoming:
                logger.debug("X-Trace-Id rejected (unsafe chars) — minting new id")
            trace_id = new_trace_id()

        _trace_id_var.set(trace_id)

        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception", extra={"trace_id": trace_id})
            raise

        response.headers[self._header] = trace_id
        return response
