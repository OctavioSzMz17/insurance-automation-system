"""Tool registry + LLM spec generator.

Each wrapper function in core/tools/<domain>.py is decorated with @mcp_tool(...).
The decorator captures: domain, tool name (the actual MCP tool), human description,
LLM-visible input schema, and a `write_restricted` flag.

  - `build_tool_specs(include_writes=False)` → tool dicts shown to the LLM.
    Defaults to read-only. The auditor and analyst NEVER call this with include_writes=True.
    Only `agents/mutation_handler.py` invokes the writes-enabled flavour, and it does so
    OUTSIDE the LLM loop (the LLM never sees write tools in its spec).
  - `dispatch(pool, name, args, *, allow_writes=False)` → invoke the underlying MCP tool.
    Refuses to dispatch a write_restricted tool unless `allow_writes=True`. Double safety:
    the LLM can't see it, AND if it ever sneaks in by accident the dispatcher refuses.

Single source of truth: rename the wrapper, the LLM sees the new name automatically.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Final

from packages.common.exceptions import ForbiddenError

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

_REGISTRY: dict[str, "ToolBinding"] = {}
_NAME_SEPARATOR: Final[str] = "__"


@dataclass(frozen=True)
class ToolBinding:
    llm_name: str            # the name the LLM sees (e.g. "claims__get_claim_by_id")
    domain: str              # MCP domain ("claims")
    mcp_tool: str            # the actual tool name on the MCP server
    description: str
    input_schema: dict[str, Any]
    handler: ToolFn          # the async wrapper function in core/tools/<domain>.py
    write_restricted: bool = False


def mcp_tool(
    *,
    domain: str,
    mcp_tool: str,
    description: str,
    input_schema: dict[str, Any],
    write_restricted: bool = False,
):
    """Decorator. Registers the wrapper as the canonical orchestrator-side tool.

    `write_restricted=True` means the tool mutates state and must:
      - NOT appear in LLM-facing tool specs (build_tool_specs default omits it),
      - only be dispatchable when the caller explicitly opts into writes,
      - be guarded server-side by the MCP via `@write_restricted` + `MCP_WRITE_ENABLED=1`.
    """

    def deco(fn: ToolFn) -> ToolFn:
        llm_name = f"{domain}{_NAME_SEPARATOR}{mcp_tool}"
        if llm_name in _REGISTRY:
            raise RuntimeError(f"duplicate tool registration: {llm_name}")
        _REGISTRY[llm_name] = ToolBinding(
            llm_name=llm_name,
            domain=domain,
            mcp_tool=mcp_tool,
            description=description,
            input_schema=input_schema,
            handler=fn,
            write_restricted=write_restricted,
        )

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return await fn(*args, **kwargs)

        wrapper._binding = _REGISTRY[llm_name]  # type: ignore[attr-defined]
        return wrapper

    return deco


def build_tool_specs(*, include_writes: bool = False) -> list[dict[str, Any]]:
    """Tool specs for the LLM. Defaults to read-only (Rule 4 — agents see no writes)."""
    return [
        {
            "name": b.llm_name,
            "description": b.description,
            "input_schema": b.input_schema,
        }
        for b in _REGISTRY.values()
        if include_writes or not b.write_restricted
    ]


def split_tool_name(llm_name: str) -> tuple[str, str]:
    domain, _, mcp_tool = llm_name.partition(_NAME_SEPARATOR)
    if not domain or not mcp_tool:
        raise ValueError(f"invalid tool name: {llm_name}")
    return domain, mcp_tool


def is_write_restricted(llm_name: str) -> bool:
    b = _REGISTRY.get(llm_name)
    return bool(b and b.write_restricted)


async def dispatch(
    pool: Any, llm_name: str, arguments: dict[str, Any], *, allow_writes: bool = False
) -> dict[str, Any]:
    """Invoke the underlying MCP tool by the LLM-facing name.

    Refuses any write tool unless `allow_writes=True`. This is the second line of defense
    after the LLM-spec hiding — if a write tool name somehow appears in a tool_use block,
    the dispatcher still rejects it.
    """
    binding = _REGISTRY.get(llm_name)
    if binding is None:
        raise ValueError(f"unknown tool: {llm_name}")
    if binding.write_restricted and not allow_writes:
        raise ForbiddenError(f"tool {llm_name!r} is write-restricted; not callable from this context")
    return await pool.call(binding.domain, binding.mcp_tool, arguments)
