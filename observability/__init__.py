"""Observability package — Phase D.

Public surface:

    from observability import get_tracer
    tracer = get_tracer()
    handler = tracer.callback_handler(trace_name="claim-submit",
                                      session_id=request_id,
                                      metadata={"pdf": "..."})
    await executor.ainvoke({...}, config={"callbacks": [handler]} if handler else None)

`get_tracer()` is cached for the app lifetime — one tracer per process,
not one per request. The handler it returns IS per-request, so each
trace gets its own root.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .tracers.base_tracer import BaseTracer, NoopTracer
from .tracers.langfuse_tracer import LangfuseTracer

logger = logging.getLogger(__name__)

__all__ = ["BaseTracer", "NoopTracer", "LangfuseTracer", "get_tracer"]


@lru_cache(maxsize=1)
def get_tracer() -> BaseTracer:
    """Pick a tracer based on current settings; safe to call repeatedly.

    Returns a NoopTracer when Langfuse is not configured — no exception,
    no warning spam. The rest of the system stays vendor-agnostic and
    keeps working with no observability.
    """
    # Late import to avoid a hard dep on the orchestrator package — the
    # observability package should also work from tests + scripts.
    try:
        from config import get_settings  # type: ignore
    except ImportError:
        logger.info("observability.get_tracer: orchestrator config not "
                    "importable — defaulting to NoopTracer")
        return NoopTracer()

    settings = get_settings()
    if not settings.langfuse_enabled:
        logger.info("observability.get_tracer: Langfuse not configured "
                    "(missing host/public/secret key) — NoopTracer")
        return NoopTracer()

    tracer = LangfuseTracer(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
    )
    return tracer if tracer.enabled else NoopTracer()
