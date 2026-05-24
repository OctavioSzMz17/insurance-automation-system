"""Policies MCP Server — SSE transport (Rule 7).

Single-domain read-only service for policy records.
Replaced stdio transport with Starlette/Uvicorn SSE (Fix 1).

Transport: HTTP/SSE via SseServerTransport on MCP_PORT (default 8003).
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))      # mcp-servers/ — Master Patch Part 1
sys.path.insert(0, str(_HERE.parents[1]))

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
import mcp.types as types

from _common.auth import MCPAuthMiddleware  # Master Patch Part 1

from tools.get_policies_by_user_id import get_policies_by_user_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

server: Server = Server("policies")

TOOLS: list[types.Tool] = [
    types.Tool(
        name="get_policies_by_user_id",
        description="Fetch all insurance policies belonging to a user by integer user_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "User primary key."}
            },
            "required": ["user_id"],
        },
    ),
]


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    logger.info(f"call_tool | name={name} arguments={arguments}")
    try:
        if name == "get_policies_by_user_id":
            result = await get_policies_by_user_id(int(arguments["user_id"]))
        else:
            raise ValueError(f"Unknown tool: '{name}'")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    except ValueError as e:
        # Business error — Fix C: tag with "kind": "business".
        logger.warning(f"call_tool business error | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e), "kind": "business"}),
        )]
    except RuntimeError as e:
        # Internal error — Fix C: tag with "kind": "internal".
        logger.error(f"call_tool internal error | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e), "kind": "internal"}),
        )]


# ── SSE transport wiring ──────────────────────────────────────────

sse_transport = SseServerTransport("/messages/")


async def handle_sse(scope: Any, receive: Any, send: Any) -> None:
    async with sse_transport.connect_sse(scope, receive, send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


starlette_app = Starlette(
    debug=False,
    middleware=[Middleware(MCPAuthMiddleware)],  # Master Patch Part 1
    routes=[
        Mount("/sse", app=handle_sse),
        Mount("/messages", app=sse_transport.handle_post_message),
    ],
)

if __name__ == "__main__":
    # Fix #92 — default to loopback; operators set MCP_HOST=0.0.0.0 via
    # ConfigMap/docker-compose when the pod must be reachable by peers.
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8003"))
    logger.info(f"Policies MCP server starting | transport=SSE host={host} port={port}")
    uvicorn.run(starlette_app, host=host, port=port)
