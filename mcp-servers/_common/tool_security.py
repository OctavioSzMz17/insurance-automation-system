"""Marker decorator for write-capable tools (Rule 3, Rule 4).

Phase 1 invariant: NO tool wears this decorator. It is defined so Phase 2 (auditor) can
opt-in mutations without re-architecting. The presence of `_write_restricted = True`
allows runtime guards (e.g., in `server_wiring`) to enforce that write tools are only
exposed when an explicit env flag (`MCP_WRITE_ENABLED=1`) is set.
"""
from __future__ import annotations

import functools
import os
from typing import Any, Awaitable, Callable, TypeVar

from packages.common.exceptions import ForbiddenError

T = TypeVar("T")


def write_restricted(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Wrap a tool handler so it refuses to execute unless writes are explicitly enabled."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        if os.environ.get("MCP_WRITE_ENABLED", "0") != "1":
            raise ForbiddenError("write operations are disabled in this deployment")
        return await func(*args, **kwargs)

    wrapper._write_restricted = True  # type: ignore[attr-defined]
    return wrapper


def is_write_restricted(func: object) -> bool:
    return bool(getattr(func, "_write_restricted", False))
