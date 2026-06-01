"""Typed wrappers over the MCP client pool, one module per MCP domain.

This layer is the orchestrator-side contract with each MCP. The agent calls these
functions; the LLM tool spec is generated from them. There is exactly ONE place where
each tool's name, args, and signature live — change it here, and both the agent path
and the LLM-facing schema move in lockstep.

`core/tools/*` is NOT the MCP server. The MCP server is in `mcp-servers/<domain>/`.
This module talks to it over SSE through the `MCPClientPool`.
"""
from __future__ import annotations

from typing import Any

from . import claims, documents, policies, users
from ._spec import (
    ToolBinding,
    build_tool_specs,
    dispatch,
    is_write_restricted,
    split_tool_name,
)

__all__ = [
    "claims",
    "documents",
    "policies",
    "users",
    "ToolBinding",
    "build_tool_specs",
    "dispatch",
    "is_write_restricted",
    "split_tool_name",
]
