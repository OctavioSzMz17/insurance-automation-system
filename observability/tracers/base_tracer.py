"""The tracing contract every backend implements (Rule 9).

Design notes worth knowing before adding a backend:

**Why `BaseTracer` is a class and not just a Protocol.** The mechanics that must not
diverge between backends — parent/child linkage, timing, exception capture, masking,
error isolation — live here once. A backend overrides `on_start` / `on_end` and nothing
else. A Protocol alone would let two backends disagree about what a span *means*.

**Why `SpanKind.GENERATION` exists.** A generic tracer needs only a name and attributes.
Cost, however, is a function of (model, input tokens, output tokens), so the abstraction
has to admit that some spans are model calls. OpenTelemetry's GenAI semantic conventions
make the same concession with their `gen_ai.*` attributes, which is why this contract maps
cleanly onto both Langfuse and OTLP.

**Why two clocks.** `started_at` / `ended_at` are `time.time()` because they are
cross-process correlation keys and Rule 14 is explicit that `time.monotonic()` is
meaningless outside one process. Duration, which is a local measurement, comes from a
monotonic reading so an NTP step cannot produce a negative span.

**Why tracing fails open.** The verification gate fails closed because letting a
fraudulent claim through is worse than refusing a valid one. Observability is the
opposite: a tracer that fails closed takes down the product it was meant to observe. So
every backend callback is wrapped, and a failing backend degrades to no tracing.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from observability.logging import get_logger

_logger = get_logger(__name__)


class SpanKind(StrEnum):
    """What a span represents. Backends map these onto their own vocabulary."""

    INTERNAL = "internal"      # a graph node, a deterministic check, the mutation gate
    GENERATION = "generation"  # an LLM call — the only kind that carries usage and cost
    TOOL = "tool"              # an MCP tool invocation
    EVENT = "event"            # a point in time, no duration


@dataclass
class SpanRecord:
    """The full payload of one span. This is what backends serialise.

    It is also, deliberately, exactly what the live UI needs to render one orb's detail
    panel — the most demanding consumer, so satisfying it satisfies the others.
    """

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    started_at: float
    session_id: str | None = None
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    input: Any = None
    output: Any = None
    usage: dict[str, Any] | None = None
    ok: bool = True
    message: str | None = None
    scores: list[dict[str, Any]] = field(default_factory=list)
    # Monotonic reading taken at creation; never serialised.
    _t0: float = field(default_factory=time.monotonic, repr=False)
    _duration_ms: float | None = field(default=None, repr=False)

    @property
    def duration_ms(self) -> float | None:
        return self._duration_ms

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": str(self.kind),
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": round(self._duration_ms, 2) if self._duration_ms is not None else None,
            "ok": self.ok,
            "attributes": self.attributes,
        }
        for key in ("input", "output", "usage", "message"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.scores:
            payload["scores"] = self.scores
        return payload


class Span:
    """Handle handed to instrumented code. Mutates the underlying record."""

    __slots__ = ("_record", "_mask")

    def __init__(self, record: SpanRecord, mask: Callable[[Any], Any]) -> None:
        self._record = record
        self._mask = mask

    @property
    def span_id(self) -> str:
        return self._record.span_id

    @property
    def trace_id(self) -> str:
        return self._record.trace_id

    @property
    def record(self) -> SpanRecord:
        return self._record

    def set_attributes(self, **attrs: Any) -> None:
        """Low-cardinality facts about this step. Safe for filtering and grouping."""
        self._record.attributes.update(self._mask(attrs))

    def set_session(self, session_id: str) -> None:
        """Attach this span to a session after the fact.

        `/audit` and `/confirm` only learn the pipeline_id after loading the stored
        proposal, so they open their root span first and join the session once they
        know which claim they are working on. Children created afterwards inherit it.
        """
        self._record.session_id = session_id

    def set_input(self, value: Any) -> None:
        self._record.input = self._mask(value)

    def set_output(self, value: Any) -> None:
        self._record.output = self._mask(value)

    def set_usage(self, *, input_tokens: int, output_tokens: int, model: str) -> None:
        """Token counts for a GENERATION span. Backends derive cost from this."""
        self._record.usage = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(input_tokens) + int(output_tokens),
            "model": model,
        }

    def set_status(self, ok: bool, *, message: str | None = None) -> None:
        self._record.ok = ok
        if message is not None:
            self._record.message = str(self._mask(message))

    def score(self, name: str, value: float | bool | str, *, comment: str | None = None) -> None:
        """Attach a score. Scores are what turn traces into tracked metrics."""
        entry: dict[str, Any] = {"name": name, "value": value}
        if comment is not None:
            entry["comment"] = str(self._mask(comment))
        self._record.scores.append(entry)


@runtime_checkable
class Tracer(Protocol):
    """What instrumented code depends on. Nothing else."""

    def trace(self, name: str, *, session_id: str | None = ..., **attrs: Any) -> Any: ...
    def span(self, name: str, *, kind: SpanKind = ..., **attrs: Any) -> Any: ...


class BaseTracer:
    """Shared span mechanics. Backends override `on_start` / `on_end`."""

    def __init__(self, *, mask: Callable[[Any], Any] | None = None) -> None:
        self._mask = mask or (lambda value: value)
        self._degraded = False

    # ── public surface ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def trace(
        self, name: str, *, session_id: str | None = None, **attrs: Any
    ) -> AsyncIterator[Span]:
        """Open a root span. Children attach to it automatically.

        `session_id` groups several traces into one timeline — for a claim it is the
        pipeline_id, so /analyze, /audit and /confirm read as one story rather than
        three unrelated executions.
        """
        async with self._open(name, kind=SpanKind.INTERNAL, root=True,
                              session_id=session_id, attrs=attrs) as span:
            yield span

    @asynccontextmanager
    async def span(
        self, name: str, *, kind: SpanKind = SpanKind.INTERNAL, **attrs: Any
    ) -> AsyncIterator[Span]:
        """Open a child of whatever span is currently active."""
        async with self._open(name, kind=kind, root=False, session_id=None, attrs=attrs) as span:
            yield span

    # ── backend hooks ───────────────────────────────────────────────────────

    def on_start(self, record: SpanRecord) -> None:  # noqa: B027 — optional hook
        """Called when a span opens. Backends that stream override this."""

    def on_end(self, record: SpanRecord) -> None:  # noqa: B027 — optional hook
        """Called when a span closes, successfully or not."""

    def shutdown(self) -> None:  # noqa: B027 — optional hook
        """Flush anything buffered. Called from the app lifespan on the way down.

        Backends that batch — which is every network-bound one — lose the tail of a
        short-lived process without this.
        """

    # ── internals ───────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _open(
        self,
        name: str,
        *,
        kind: SpanKind,
        root: bool,
        session_id: str | None,
        attrs: dict[str, Any],
    ) -> AsyncIterator[Span]:
        from observability.context import _CURRENT_SPAN  # local import breaks the cycle

        parent = None if root else _CURRENT_SPAN.get()
        record = SpanRecord(
            trace_id=parent.trace_id if parent is not None else uuid.uuid4().hex,
            span_id=uuid.uuid4().hex,
            parent_id=parent.span_id if parent is not None else None,
            name=name,
            kind=kind,
            started_at=time.time(),
            session_id=session_id if session_id is not None
            else (parent.session_id if parent is not None else None),
        )
        span = Span(record, self._mask)
        if attrs:
            span.set_attributes(**attrs)

        token = _CURRENT_SPAN.set(record)
        self._safely("on_start", record)
        try:
            yield span
        except Exception as exc:
            span.set_status(False, message=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            record._duration_ms = (time.monotonic() - record._t0) * 1000.0
            record.ended_at = time.time()
            _CURRENT_SPAN.reset(token)
            self._safely("on_end", record)

    def _safely(self, hook: str, *args: Any) -> None:
        """Run a backend hook without ever letting it break the traced code.

        Takes *args because `shutdown` has no record — passing one would raise a
        TypeError that the guard below would then swallow, leaving the flush silently
        unexecuted.
        """
        try:
            getattr(self, hook)(*args)
        except Exception as exc:  # noqa: BLE001 — observability must fail open
            if not self._degraded:
                self._degraded = True
                _logger.warning(
                    "tracing backend failed; continuing untraced",
                    extra={"event": "tracing.degraded", "hook": hook,
                           "tracer": type(self).__name__, "err": repr(exc)},
                )


__all__ = ["BaseTracer", "Span", "SpanKind", "SpanRecord", "Tracer"]
