"""Ambient tracer and current-span context.

Two separate pieces of state, for two reasons:

* The **tracer** is a process-wide singleton configured once in the app lifespan. Passing
  it through `Analyst → compiled graph → node function` would have meant threading a
  parameter through every signature in the pipeline for no benefit.
* The **current span** is a `ContextVar`, so a child span finds its parent without being
  told. `ContextVar` is per-task, so two claims analysed concurrently never adopt each
  other's spans — which a module-level variable would have done silently.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from observability.tracers.base_tracer import SpanRecord, Tracer

_CURRENT_SPAN: ContextVar["SpanRecord | None"] = ContextVar("observability_current_span", default=None)

_TRACER: "Tracer | None" = None


def configure_tracing(
    backend: str | None = None,
    *,
    environment: str | None = None,
    mask_level: str | None = None,
    **backend_kwargs: Any,
) -> "Tracer":
    """Install the process tracer. Call once, from the app lifespan.

    `backend` defaults to `OBSERVABILITY_BACKEND`, itself defaulting to `noop`, so a
    deployment that has not opted into tracing pays nothing and nothing breaks.
    """
    global _TRACER
    from observability.tracers import build_tracer

    _TRACER = build_tracer(
        backend, environment=environment, mask_level=mask_level, **backend_kwargs
    )
    return _TRACER


def get_tracer() -> "Tracer":
    """The active tracer, or a no-op one if tracing was never configured."""
    global _TRACER
    if _TRACER is None:
        from observability.tracers.noop_tracer import NoopTracer

        _TRACER = NoopTracer()
    return _TRACER


def set_tracer(tracer: "Tracer") -> None:
    """Install a tracer directly. Used by tests and by the eval harness."""
    global _TRACER
    _TRACER = tracer


def shutdown_tracing() -> None:
    """Flush buffered spans. Call from the app lifespan on the way down.

    Network-bound backends batch, so without this a short-lived process loses the tail
    of its spans — including, typically, the ones explaining why it shut down.
    """
    global _TRACER
    if _TRACER is None:
        return
    shutdown = getattr(_TRACER, "shutdown", None)
    if shutdown is not None:
        shutdown()


def reset_tracing() -> None:
    """Forget the configured tracer. Test-isolation helper."""
    global _TRACER
    _TRACER = None


def current_span() -> "SpanRecord | None":
    """The innermost open span, if any."""
    return _CURRENT_SPAN.get()


def current_trace_id() -> str | None:
    span = _CURRENT_SPAN.get()
    return span.trace_id if span is not None else None


__all__ = [
    "configure_tracing",
    "current_span",
    "current_trace_id",
    "get_tracer",
    "reset_tracing",
    "set_tracer",
    "shutdown_tracing",
]
