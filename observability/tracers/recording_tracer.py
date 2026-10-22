"""In-memory backend for tests and the eval harness.

Instrumentation rots silently: someone refactors a node, the `async with` goes away, and
nothing fails because no test looked at the spans. This backend is how the span tree
becomes assertable — `evals/test_tracing.py` uses it to pin the shape of a full claim.
"""
from __future__ import annotations

from typing import Any

from observability.tracers.base_tracer import BaseTracer, SpanRecord


class RecordingTracer(BaseTracer):
    """Keeps every span, in completion order, for inspection."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.started: list[SpanRecord] = []
        self.finished: list[SpanRecord] = []

    def on_start(self, record: SpanRecord) -> None:
        self.started.append(record)

    def on_end(self, record: SpanRecord) -> None:
        self.finished.append(record)

    # ── query helpers ───────────────────────────────────────────────────────

    def names(self) -> list[str]:
        return [r.name for r in self.finished]

    def by_name(self, name: str) -> list[SpanRecord]:
        return [r for r in self.finished if r.name == name]

    def one(self, name: str) -> SpanRecord:
        matches = self.by_name(name)
        if len(matches) != 1:
            raise AssertionError(
                f"expected exactly one {name!r} span, found {len(matches)}: {self.names()}"
            )
        return matches[0]

    def roots(self) -> list[SpanRecord]:
        return [r for r in self.finished if r.parent_id is None]

    def children_of(self, record: SpanRecord) -> list[SpanRecord]:
        return [r for r in self.finished if r.parent_id == record.span_id]

    def tree(self) -> str:
        """An indented rendering, for putting in an assertion message."""
        lines: list[str] = []

        def walk(record: SpanRecord, depth: int) -> None:
            duration = f"{record.duration_ms:.0f}ms" if record.duration_ms is not None else "—"
            status = "" if record.ok else "  [FAILED]"
            lines.append(f"{'  ' * depth}{record.name} ({duration}){status}")
            for child in self.children_of(record):
                walk(child, depth + 1)

        for root in self.roots():
            walk(root, 0)
        return "\n".join(lines)

    def clear(self) -> None:
        self.started.clear()
        self.finished.clear()
