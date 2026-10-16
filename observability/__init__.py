"""Observability: structured logging and tracing for the whole monorepo (Rule 9).

The contract Rule 9 sets is that no service ever imports a tracing SDK. Agents, routes
and MCPs talk to the `Tracer` protocol in `tracers/base_tracer.py`; a backend is a new
file under `tracers/` and a different value of `OBSERVABILITY_BACKEND`. Swapping Langfuse
for OpenTelemetry, or running both, must not touch a single call site.

Typical use:

    from observability import SpanKind, get_tracer

    async with get_tracer().span("analyst.think_act", kind=SpanKind.GENERATION) as span:
        turn = await llm.complete(...)
        span.set_usage(input_tokens=turn.input_tokens, output_tokens=turn.output_tokens,
                       model=llm.model)

`get_tracer()` returns a no-op until `configure_tracing()` runs in the app lifespan, so
importing instrumented modules is always safe — tests and scripts need no setup.
"""
from __future__ import annotations

from .context import (
    configure_tracing,
    current_span,
    get_tracer,
    reset_tracing,
    shutdown_tracing,
)
from .logging import configure as configure_logging
from .logging import get_logger
from .tracers.base_tracer import Span, SpanKind, SpanRecord, Tracer

__all__ = [
    "Span",
    "SpanKind",
    "SpanRecord",
    "Tracer",
    "configure_logging",
    "configure_tracing",
    "current_span",
    "get_logger",
    "get_tracer",
    "reset_tracing",
    "shutdown_tracing",
]
