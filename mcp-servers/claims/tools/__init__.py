"""Claims MCP tool implementations.

Tools:
    create_claim  — @write_restricted, callable by auditor_agent only

The @write_restricted decorator is defined here (used as a marker by the
MCP router in C1.5 to enforce Rule 3 / Rule 7 role-based access control).
"""

from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def write_restricted(func: F) -> F:
    """Mark a tool as performing a database write (Rule 3).

    Tools wrapped with this decorator may only be invoked by agents
    whose role contains "WRITE" (i.e. auditor_agent).  Enforcement
    happens centrally in mcp_router.py (C1.5).  For now the decorator
    only sets an attribute that the router will inspect.
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)

    wrapper.__write_restricted__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
