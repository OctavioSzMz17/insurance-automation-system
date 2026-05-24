"""Documents MCP Server — SSE transport (Rule 7).

Exposes PDF extraction tools over HTTP/SSE.
Replaced stdio transport with Starlette/Uvicorn SSE (Fix 1).

Transport: HTTP/SSE via SseServerTransport on MCP_PORT (default 8005).

Rules applied:
    Rule 3  — Read-only domain (PDF parsing)
    Rule 7  — SSE transport; no stdio
    Rule 9  — Structured logging; no print()
    Rule 11 — Type hints throughout
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
from starlette.routing import Mount, Route
import mcp.types as types

from _common.auth import MCPAuthMiddleware  # Master Patch Part 1

from tools.extract_claim_from_pdf import extract_claim_from_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

server: Server = Server("documents")

TOOLS: list[types.Tool] = [
    types.Tool(
        name="extract_claim_from_pdf",
        description=(
            "Parse a PDF claim report and extract structured claim data. "
            "Returns a dict with claim_id, user_id, insured_name, incident_date, "
            "description, location, damage_summary, and reported_damage_amount."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": "Absolute filesystem path to the PDF file.",
                }
            },
            "required": ["pdf_path"],
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
        if name == "extract_claim_from_pdf":
            result = await extract_claim_from_pdf(str(arguments["pdf_path"]))
        else:
            raise ValueError(f"Unknown tool: '{name}'")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    except FileNotFoundError as e:
        # Business error — PDF not found at the given path; caller can handle.
        # Fix C: "kind": "business" so orchestrator raises ValueError → 404.
        logger.warning(f"call_tool file not found | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e), "kind": "business"}),
        )]
    except ValueError as e:
        # Business error — Fix C: tag with "kind": "business".
        logger.warning(f"call_tool business error | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e), "kind": "business"}),
        )]
    except Exception as e:
        # Internal / unexpected error — Fix C: tag with "kind": "internal"
        # so raw exception detail is never forwarded to the HTTP client.
        logger.error(f"call_tool internal error | tool={name} error={e}", exc_info=True)
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": f"Unexpected error in {name}", "kind": "internal"}),
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
        Route("/sse", endpoint=handle_sse),
        Mount("/messages", app=sse_transport.handle_post_message),
    ],
)

if __name__ == "__main__":
    # Fix #92 — default to loopback; operators set MCP_HOST=0.0.0.0 via
    # ConfigMap/docker-compose when the pod must be reachable by peers.
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8005"))
    logger.info(f"Documents MCP server starting | transport=SSE host={host} port={port}")
    uvicorn.run(starlette_app, host=host, port=port)
