"""Wire MCP SDK tool dispatch from a `{name → ToolSpec}` registry.

This file is the single point of contact with the MCP SDK's tool API. Each domain MCP
declares its tools as ToolSpec entries and hands them to `register_tools(server, specs)`.

Centralizing this avoids divergence between MCPs (Rule 1: no duplication) and ensures
every tool flows through the `@classified` error wrapper (Rule 7).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from mcp.server import Server
from mcp.types import TextContent, Tool

from .errors import classified
from .logging import get_logger
from .tool_security import is_write_restricted

_logger = get_logger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


def register_tools(server: Server, specs: Sequence[ToolSpec]) -> None:
    """Attach the SDK's list_tools / call_tool handlers using the provided specs."""

    if len({s.name for s in specs}) != len(specs):
        raise ValueError("tool names must be unique within a server")

    spec_by_name: Mapping[str, ToolSpec] = {s.name: s for s in specs}

    # Wrap each handler with the error classifier exactly once at registration time.
    wrapped: dict[str, ToolHandler] = {
        name: classified(s.handler) for name, s in spec_by_name.items()
    }

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
            for s in spec_by_name.values()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        spec = spec_by_name.get(name)
        if spec is None:
            payload = {"error": f"unknown tool: {name}", "kind": "business"}
            return [TextContent(type="text", text=json.dumps(payload))]
        if is_write_restricted(spec.handler):
            _logger.info(
                "write-restricted tool invoked",
                extra={"event": "mcp.tool.write_restricted", "tool": name},
            )
        result = await wrapped[name](arguments or {})
        return [TextContent(type="text", text=json.dumps(result, default=str))]
