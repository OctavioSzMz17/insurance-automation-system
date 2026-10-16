"""The default backend: correct structure, no output.

It still builds `SpanRecord`s and maintains parent/child linkage rather than
short-circuiting, so instrumented code behaves identically whether or not a real backend
is attached. The cost is a dataclass and two dict allocations per span — against an LLM
call, unmeasurable, and the alternative is tracing bugs that only appear once tracing is
switched on.
"""
from __future__ import annotations

from observability.tracers.base_tracer import BaseTracer


class NoopTracer(BaseTracer):
    """Discards every span."""
