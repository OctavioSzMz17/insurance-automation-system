"""Abstract tracer interface — Phase D2.1.

The orchestrator only ever talks to `BaseTracer`. The concrete
implementation (Langfuse today, possibly OpenTelemetry tomorrow) is
chosen by `observability.get_tracer()`. This keeps the agents free of
any vendor SDK import — Rule 11 (testability + swap-ability).

The interface is intentionally tiny: a tracer's job from the agents'
perspective is to hand out **LangChain callback handlers** that the
LangGraph executors can attach. Everything else (cost calc, retry
trees, prompt diffs) is the tracer's internal concern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional


class BaseTracer(ABC):
    """Vendor-agnostic tracer contract."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether traces are actually being shipped anywhere."""

    @abstractmethod
    def callback_handler(
        self,
        *,
        trace_name: str,
        user_id:    Optional[str] = None,
        session_id: Optional[str] = None,
        tags:       Optional[list[str]] = None,
        metadata:   Optional[Mapping[str, Any]] = None,
    ) -> Optional[Any]:
        """Return a per-request LangChain callback handler.

        Pass the result into
        ``executor.ainvoke(..., config={"callbacks": [handler]})``.
        Implementations may return ``None`` when tracing is disabled —
        callers must always tolerate that.

        Args:
            trace_name: Human-readable label shown in the dashboard
                        (e.g. "claim-submit", "claim-reanalyze").
            user_id:    Adjuster identity (JWT sub) for filtering.
            session_id: A correlation key — usually the orchestrator
                        request_id so all agent calls for one request
                        land under the same parent trace.
            tags:       Free-form labels for filtering (e.g. ["analyst", "v3"]).
            metadata:   Structured key/value for filtering & display.
        """

    @abstractmethod
    def flush(self) -> None:
        """Block until every queued event has been shipped.

        Important at process shutdown so we don't lose the tail of
        recent traces; cheap at request end too.
        """


# ── No-op fallback ────────────────────────────────────────────────

class NoopTracer(BaseTracer):
    """Used when no tracer backend is configured.

    Returns ``None`` callbacks (LangChain accepts that), `flush()` is
    a no-op, and `enabled` is False. The rest of the app behaves
    exactly the same — just without observability.
    """

    @property
    def enabled(self) -> bool:
        return False

    def callback_handler(self, **_: Any) -> None:
        return None

    def flush(self) -> None:
        return None
