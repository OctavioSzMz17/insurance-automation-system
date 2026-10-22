"""Emit spans to the structured JSON log.

This is the backend that makes phase 1 useful on its own: full traces, correlatable by
`trace_id`, with zero dependencies and nothing to deploy. It is also the backend to reach
for when a trace store is misbehaving, since it is the one that cannot be the problem.

Only `on_end` emits. A span's duration, status and output are not known at start, and one
line per span beats two that have to be stitched together by hand.
"""
from __future__ import annotations

from observability.logging import get_logger
from observability.tracers.base_tracer import BaseTracer, SpanRecord

_logger = get_logger("observability.trace")


class LogTracer(BaseTracer):
    """Writes one structured log line per completed span."""

    def on_end(self, record: SpanRecord) -> None:
        # The payload is nested under one key rather than splatted into `extra`:
        # `LogRecord` reserves `name`, `message`, `module` and friends, and logging
        # raises rather than overwriting them. Nesting also means a new span field can
        # never collide with a reserved attribute later.
        _logger.info(
            "span %s (%.1f ms)", record.name, record.duration_ms or 0.0,
            extra={
                "event": "trace.span",
                "trace_id": record.trace_id,
                "span_name": record.name,
                "span": record.as_dict(),
            },
        )
