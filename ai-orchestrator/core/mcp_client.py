"""Async SSE client for out-of-process MCP tool invocation (Rule 7).

Replaces the in-process importlib loader (_loader.py, eradicated in Fix 1).
Every MCP call goes over HTTP/SSE — never in-process file imports.

Master Patch Part 1 — every outbound request now carries
`X-MCP-Secret`, the shared HMAC token validated by the MCP servers'
MCPAuthMiddleware. Without it the server returns 403 before the SSE
upgrade completes.

Usage:
    from core.mcp_client import call_mcp_tool

    result = await call_mcp_tool(
        url="http://mcp-claims:8002/sse",
        tool_name="get_claim_by_id",
        arguments={"claim_id": 42},
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

# SSE connection timeout — time to establish the HTTP+SSE handshake.
_CONNECT_TIMEOUT: float = 10.0
# Read timeout — max wait for the tool to respond after the call is sent.
_READ_TIMEOUT: float = 30.0


# ── Master Patch Part 4 — sanitised exception envelope ──────────────

class MCPClientError(Exception):
    """Transport / protocol failure while invoking an MCP tool.

    Splits the verbose internal detail (URL, exception chain, traceback)
    from a safe public message suitable for a user-facing HTTP body.

    Use:
        raise MCPClientError(
            public=f"Upstream service '{tool}' is temporarily unavailable",
            internal=f"MCP call failed [{tool}@{url}]: {exc!r}",
        )

    Routing layer:
        except MCPClientError as e:
            logger.error("...", extra={"internal": e.internal}, exc_info=True)
            raise HTTPException(status_code=502, detail=e.public)

    The internal string is what `str(e)` returns (so default logging is
    verbose); the public string is what the FastAPI handler must echo
    back to the client — never the internal string.
    """

    def __init__(self, public: str, internal: str) -> None:
        super().__init__(internal)
        self.public: str = public
        self.internal: str = internal

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return f"MCPClientError(public={self.public!r}, internal={self.internal!r})"


def _auth_headers() -> dict[str, str]:
    """Build the auth header dict sent with every MCP SSE connection.

    Resolved lazily so an orchestrator misconfiguration surfaces at first
    MCP call (clear stack trace) rather than at module import.
    """
    # Local import keeps mcp_client importable in test contexts that
    # mock config without bringing pydantic-settings into scope.
    from config import get_settings

    secret = get_settings().mcp_shared_secret
    if not secret:
        raise RuntimeError(
            "MCP_SHARED_SECRET is not configured for the orchestrator. "
            "Set it in .env — every MCP server requires it on the "
            "X-MCP-Secret header (Master Patch Part 1)."
        )
    return {"X-MCP-Secret": secret}


async def call_mcp_tool(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Invoke one MCP tool over SSE and return its parsed result.

    Opens a fresh SSE connection per call, initialises the MCP session,
    calls the tool, and tears down the connection cleanly. Connection
    pooling / persistent sessions are a future optimisation.

    Args:
        url:        Full SSE endpoint, e.g. "http://mcp-claims:8002/sse".
        tool_name:  MCP tool name as declared in the server's TOOLS list.
        arguments:  Dict of named arguments matching the tool's inputSchema.

    Returns:
        Parsed Python value from the tool's JSON TextContent response.
        Returns an empty dict when the server returns no content.

    Raises:
        ValueError:      Tool returned {"error": ...} — caller/business error
                         (e.g. "Claim 42 not found"). Safe to surface verbatim.
        MCPClientError:  Transport failure, parsing failure, or MCP-level
                         isError. The internal attribute carries the full
                         traceback for logging; the public attribute carries
                         a safe sanitised message for HTTP bodies.
    """
    logger.debug(
        "call_mcp_tool | url=%s tool=%s args_keys=%s",
        url, tool_name, list(arguments.keys()),
    )
    # ── Transport layer ─────────────────────────────────────────────
    try:
        async with sse_client(
            url,
            headers=_auth_headers(),  # Master Patch Part 1 — X-MCP-Secret
            timeout=_CONNECT_TIMEOUT,
            sse_read_timeout=_READ_TIMEOUT,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    except Exception as exc:
        # Master Patch Part 4 — keep URL / exception class / chain in logs
        # only. Public message is generic to avoid leaking topology.
        logger.error(
            "call_mcp_tool transport failure | url=%s tool=%s err=%r",
            url, tool_name, exc, exc_info=True,
        )
        raise MCPClientError(
            public=f"Upstream service '{tool_name}' is temporarily unavailable",
            internal=f"transport failure [{tool_name}@{url}]: {exc!r}",
        ) from exc

    # ── MCP-level tool error (isError flag) ─────────────────────────
    if result.isError:
        raw = (
            result.content[0].text
            if result.content and hasattr(result.content[0], "text")
            else str(result.content)
        )
        logger.warning(
            "call_mcp_tool tool-level error | tool=%s content=%.500s",
            tool_name, raw,
        )
        raise MCPClientError(
            public=f"Upstream service '{tool_name}' returned an error",
            internal=f"MCP tool isError [{tool_name}@{url}]: {raw}",
        )

    if not result.content:
        return {}

    first = result.content[0]
    if not hasattr(first, "text"):
        return {}

    # ── Parsing layer ───────────────────────────────────────────────
    try:
        data = json.loads(first.text)
    except (json.JSONDecodeError, TypeError) as exc:
        # Non-JSON text content is a degenerate but valid case for some
        # tools — surface the raw text rather than fail.
        logger.debug(
            "call_mcp_tool non-JSON content | tool=%s err=%s preview=%.120s",
            tool_name, exc, first.text,
        )
        return first.text

    # Tools signal errors as {"error": "...", "kind": "business"|"internal"}.
    #
    # Fix C — dispatch on "kind" so internal DB/infra errors are sanitised
    # before they reach the client, while user-facing business errors
    # ("Claim X not found", "policy already approved") still propagate as
    # ValueError for the route to translate to 404 / 409 / etc.
    #
    #   kind == "internal"          → MCPClientError (sanitised public msg)
    #   kind == "business" or absent → ValueError (user-facing, existing behaviour)
    #
    # Servers that have not yet been updated (no "kind" field) default to
    # the "business" path so existing callers are not broken.
    if isinstance(data, dict) and "error" in data:
        if data.get("kind") == "internal":
            logger.error(
                "call_mcp_tool tool-level internal error | tool=%s detail=%.500s",
                tool_name, data["error"],
            )
            raise MCPClientError(
                public=f"Upstream service '{tool_name}' encountered an internal error",
                internal=f"tool internal error [{tool_name}@{url}]: {data['error']}",
            )
        raise ValueError(data["error"])

    return data
