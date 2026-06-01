"""Error classifier for MCP tool handlers.

Contract (Rule 7):
- ValueError / DomainError → {"error": str(e), "kind": "business"}
- Anything else            → log full detail; return {"error": "upstream_internal_error", "kind": "internal"}

The orchestrator side (`core/mcp_client.py`) reads `kind` and raises MCPClientError with a
PUBLIC string — raw SQL / stack traces never leave the MCP process.
"""
from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, TypeVar

from packages.common.enums import ErrorKind
from packages.common.exceptions import DomainError

from .logging import get_logger

_logger = get_logger(__name__)

T = TypeVar("T")
ToolResult = dict[str, Any]


def classified(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[ToolResult]]:
    """Wrap an async tool handler so its exceptions become safe dict responses."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> ToolResult:
        try:
            value = await func(*args, **kwargs)
        except (DomainError, ValueError) as exc:
            return {"error": str(exc), "kind": ErrorKind.BUSINESS.value}
        except Exception:
            _logger.exception(
                "tool internal error",
                extra={"event": "mcp.tool.internal_error", "tool": func.__name__},
            )
            return {"error": "upstream_internal_error", "kind": ErrorKind.INTERNAL.value}
        if isinstance(value, dict):
            return value
        return {"data": value}

    return wrapper
