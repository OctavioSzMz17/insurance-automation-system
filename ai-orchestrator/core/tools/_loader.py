"""ERADICATED — Fix 1 (Transport Bypass remediation, Rule 7).

This module previously loaded MCP tool Python files in-process using
importlib.util, bypassing all network isolation and service boundaries.

All MCP communication now goes through core.mcp_client.call_mcp_tool
over authenticated HTTP/SSE to the MCP servers' /sse endpoints.

Do NOT import from this module. Any remaining reference is a bug.
"""

from __future__ import annotations


def load_mcp_tool(*args: object, **kwargs: object) -> None:  # type: ignore[return]
    raise ImportError(
        "_loader.load_mcp_tool is eradicated (Fix 1 / Rule 7). "
        "Use core.mcp_client.call_mcp_tool with the MCP server's SSE URL."
    )
