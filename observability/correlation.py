"""Request-scoped trace identifier propagation — Phase D2.4.

The "tracking chip" that follows a request from the FastAPI middleware
all the way down into MCP tool calls, regardless of how many async
hops happen on the way.

Why `contextvars` and not thread-locals?
    asyncio tasks share the same OS thread but get distinct context
    copies. `ContextVar` propagates correctly across `await` boundaries
    and `asyncio.create_task` calls; thread-locals do not.

Usage:

    # FastAPI middleware (entry point)
    set_trace_id(new_trace_id())

    # Anywhere downstream — same task tree, any depth
    tid = get_trace_id()
    logger.info("processing claim", extra={"trace_id": tid})

The default value is `None` so non-request code (background jobs,
tests) doesn't accidentally inherit a stale id.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

# Module-level ContextVar so every importer shares the same handle.
# `default=None` makes "no trace_id set" an explicit, easy-to-test state.
_trace_id_var: ContextVar[Optional[str]] = ContextVar(
    "insureai_trace_id", default=None,
)


def get_trace_id() -> Optional[str]:
    """Return the trace_id bound to the current async context, or None."""
    return _trace_id_var.get()


def set_trace_id(value: Optional[str]) -> None:
    """Bind `value` (or clear) the trace_id for the current async context.

    Subsequent `await`s inherit this binding. Other concurrent requests
    keep their own binding — contextvars are per-task copies.
    """
    _trace_id_var.set(value)


def new_trace_id() -> str:
    """Generate a fresh UUID4 trace id."""
    return str(uuid.uuid4())
