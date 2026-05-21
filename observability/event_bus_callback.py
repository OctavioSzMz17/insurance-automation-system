"""LangChain → EventBus bridge — Phase E1.2.

A callback handler that turns LangChain's `on_tool_*` and `on_llm_*`
hooks into `tool.call` / `tool.result` events on our local pub/sub.
This is what makes the "Lía is thinking…" UI panel feel alive —
each MCP tool invocation shows up the moment it starts and again
when it returns, latency attached.

Attach exactly like the Langfuse handler:

    handler = EventBusCallbackHandler(request_id=rid, stage="analyst")
    await executor.ainvoke({...}, config={"callbacks": [handler]})

Both handlers can live in the same callbacks list; they're
independent.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from .event_bus import EventBus, get_event_bus, make_event

logger = logging.getLogger(__name__)

# Cap payload sizes so a huge prompt / tool dump can't blow up the
# browser when it streams in.
_MAX_PAYLOAD_CHARS = 800


def _truncate(value: Any) -> str:
    """Render anything as a string with a hard ceiling for stream safety."""
    s = value if isinstance(value, str) else repr(value)
    return s if len(s) <= _MAX_PAYLOAD_CHARS else s[:_MAX_PAYLOAD_CHARS] + "…"


class EventBusCallbackHandler(AsyncCallbackHandler):
    """Forwards LangChain tool lifecycle events to our event bus.

    Stays narrow on purpose: we only intercept tool start/end + LLM
    end (for token counts). Skip prompt content — that's what the
    full Langfuse trace is for. The live stream is a heartbeat, not
    a replay.
    """

    def __init__(
        self,
        *,
        request_id: str,
        stage:      str,            # "analyst" | "auditor"
        bus:        Optional[EventBus] = None,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._stage      = stage
        self._bus        = bus or get_event_bus()
        # Track start times per run_id so we can compute latency on end.
        self._started_at: dict[UUID, float] = {}

    # ── Tool hooks ──────────────────────────────────────────────

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str:  str,
        *,
        run_id:    UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        self._started_at[run_id] = time.perf_counter()
        tool_name = (serialized or {}).get("name") or "tool"
        self._bus.publish(
            self._request_id,
            make_event(
                "tool.call",
                self._request_id,
                stage=self._stage,
                name=tool_name,
                data={"input": _truncate(input_str)},
            ),
        )

    async def on_tool_end(
        self,
        output:  Any,
        *,
        run_id:  UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        started = self._started_at.pop(run_id, None)
        latency_ms = (
            int((time.perf_counter() - started) * 1000)
            if started is not None else None
        )
        tool_name = (kwargs.get("name")) or "tool"
        self._bus.publish(
            self._request_id,
            make_event(
                "tool.result",
                self._request_id,
                stage=self._stage,
                name=tool_name,
                data={"output": _truncate(output)},
                latency_ms=latency_ms,
            ),
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        started = self._started_at.pop(run_id, None)
        latency_ms = (
            int((time.perf_counter() - started) * 1000)
            if started is not None else None
        )
        self._bus.publish(
            self._request_id,
            make_event(
                "tool.result",
                self._request_id,
                stage=self._stage,
                name="tool",
                data={"error": _truncate(str(error))},
                latency_ms=latency_ms,
            ),
        )
