"""Langfuse backend.

Langfuse's own SDK (4.x) is OpenTelemetry-based and context-manager-first:
`start_as_current_observation` nests by entering a `with` block. `BaseTracer`, by
contrast, owns the context management and exposes `on_start` / `on_end` hooks, which is
what lets one set of instrumentation feed several backends at once.

The bridge between the two is `start_observation`, the SDK's *manual* constructor: it
creates a child of an explicit parent without touching the ambient OTel context. So this
backend keeps its own `span_id → observation` map, looks the parent up, and never fights
the SDK over who owns the current context.

Verified offline against langfuse 4.17 by swapping in an in-memory OTel exporter and
asserting the exported spans — see `tests/unit/test_langfuse_tracer.py`. That is why this
file does not need a running Langfuse to be trustworthy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from observability.logging import get_logger
from observability.tracers.base_tracer import BaseTracer, SpanKind, SpanRecord

_logger = get_logger(__name__)

#: SpanKind → Langfuse observation type. The SDK has native `tool` and `event` types,
#: so the mapping is one-to-one and nothing is flattened into a generic span.
_AS_TYPE: dict[SpanKind, str] = {
    SpanKind.INTERNAL: "span",
    SpanKind.GENERATION: "generation",
    SpanKind.TOOL: "tool",
    SpanKind.EVENT: "event",
}

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


@dataclass
class _Open:
    """A live Langfuse observation plus, for roots, the attribute scope around it."""

    observation: Any
    scope: Any | None = None


def _data_type(value: Any) -> str:
    """Langfuse needs BOOLEAN stated explicitly — a bare 1 is inferred as NUMERIC."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        return "NUMERIC"
    return "CATEGORICAL"


class LangfuseTracer(BaseTracer):
    """Ships spans to Langfuse."""

    def __init__(
        self,
        *,
        mask: Callable[[Any], Any] | None = None,
        client: Any = None,
        environment: str | None = None,
        release: str | None = None,
    ) -> None:
        super().__init__(mask=mask)
        self._client = client if client is not None else self._build_client(environment, release)
        # Named `_observations`, not `_open`: BaseTracer._open is the context
        # manager that creates spans, and shadowing it breaks every span.
        self._observations: dict[str, _Open] = {}

    @staticmethod
    def _build_client(environment: str | None, release: str | None) -> Any:
        missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            # Raised so `build_tracer` degrades to a no-op with a warning rather than
            # letting the service boot believing it is traced when it is not.
            raise ValueError(
                f"the langfuse backend needs {', '.join(missing)}; "
                "set them or choose OBSERVABILITY_BACKEND=log"
            )
        from langfuse import get_client

        client = get_client()
        _logger.info(
            "langfuse tracing enabled",
            extra={"event": "tracing.langfuse.enabled",
                   "host": os.environ.get("LANGFUSE_BASE_URL", "cloud"),
                   "environment": environment, "release": release},
        )
        return client

    # ── hooks ───────────────────────────────────────────────────────────────

    def on_start(self, record: SpanRecord) -> None:
        as_type = _AS_TYPE.get(record.kind, "span")
        parent = self._observations.get(record.parent_id) if record.parent_id else None

        if parent is not None:
            observation = parent.observation.start_observation(
                name=record.name, as_type=as_type
            )
            self._observations[record.span_id] = _Open(observation)
            return

        # A root. The session has to be in scope *before* the observation is created
        # for it to reach the whole subtree, and `propagate_attributes` is the only
        # supported way to set it — so the scope is entered here and closed in on_end.
        scope = None
        if record.session_id:
            from langfuse import propagate_attributes

            scope = propagate_attributes(
                session_id=record.session_id, trace_name=record.name
            )
            scope.__enter__()
        observation = self._client.start_observation(name=record.name, as_type=as_type)
        self._observations[record.span_id] = _Open(observation, scope)

    def on_end(self, record: SpanRecord) -> None:
        entry = self._observations.pop(record.span_id, None)
        if entry is None:
            # on_start failed for this span; nothing to close.
            return
        try:
            self._finish(entry.observation, record)
        finally:
            # The scope must close even if the update failed, or the OTel context
            # leaks into whatever runs next on this task.
            if entry.scope is not None:
                entry.scope.__exit__(None, None, None)

    def shutdown(self) -> None:
        self._client.flush()

    # ── internals ───────────────────────────────────────────────────────────

    def _finish(self, observation: Any, record: SpanRecord) -> None:
        update: dict[str, Any] = {}
        if record.attributes:
            update["metadata"] = record.attributes
        if record.input is not None:
            update["input"] = record.input
        if record.output is not None:
            update["output"] = record.output
        if record.usage:
            usage = dict(record.usage)
            model = usage.pop("model", None)
            usage.pop("total_tokens", None)  # Langfuse derives the total itself
            update["usage_details"] = usage
            if model:
                update["model"] = model
        if not record.ok:
            update["level"] = "ERROR"
            if record.message:
                update["status_message"] = record.message

        if update:
            observation.update(**update)

        for score in record.scores:
            self._emit_score(observation, score)

        observation.end()

    def _emit_score(self, observation: Any, score: dict[str, Any]) -> None:
        """Scores go to the trace, not the observation.

        `review_flags`, `gate_blocked` and `human_decision` are facts about the *claim*,
        not about the span that happened to notice them, and a trace-level score is what
        a dashboard can chart over time.

        Scores travel over the HTTP API rather than the span exporter, so a failure here
        is isolated: it must not cost us the span itself.
        """
        try:
            observation.score_trace(
                name=score["name"],
                value=score["value"],
                data_type=_data_type(score["value"]),
                comment=score.get("comment"),
            )
        except Exception as exc:  # noqa: BLE001 — observability fails open
            _logger.warning(
                "langfuse score rejected",
                extra={"event": "tracing.langfuse.score_failed",
                       "score": score.get("name"), "err": repr(exc)},
            )
