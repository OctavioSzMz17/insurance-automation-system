"""Claims MCP Server — SSE transport (Rule 7).

Exposes all claim tools (read + write-restricted).
Replaced stdio transport with Starlette/Uvicorn SSE (Fix 1).
Also completes the TOOLS list: adds list_claims and append_reasoning.

Transport: HTTP/SSE via SseServerTransport on MCP_PORT (default 8002).

Rules applied:
    Rule 3  — Domain isolation; write tools marked via @write_restricted
    Rule 7  — SSE transport; no stdio
    Rule 8  — DATABASE_URL read from .env
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

from tools.create_claim        import create_claim
from tools.get_claim_by_id     import get_claim_by_id
from tools.update_claim_status import update_claim_status
from tools.list_claims         import list_claims
from tools.append_reasoning    import append_reasoning

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

server: Server = Server("claims")

TOOLS: list[types.Tool] = [
    types.Tool(
        name="get_claim_by_id",
        description=(
            "Fetch a single claim record by its integer claim_id. "
            "Read-only."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id": {"type": "integer", "description": "Claim primary key."}
            },
            "required": ["claim_id"],
        },
    ),
    types.Tool(
        name="list_claims",
        description=(
            "Return a paginated list of claim rows ordered most-recent-first. "
            "Optional status filter. Read-only."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": ["string", "null"],
                    "description": "Optional claim_status filter (e.g. MANUAL_REVIEW).",
                },
                "limit":  {"type": "integer", "description": "Max rows (1-500).", "default": 100},
                "offset": {"type": "integer", "description": "Skip N rows.", "default": 0},
            },
        },
    ),
    types.Tool(
        name="update_claim_status",
        description=(
            "Move a claim from MANUAL_REVIEW/AWAITING_INPUT to APPROVED or REJECTED. "
            "WRITE-RESTRICTED."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id":   {"type": "integer"},
                "new_status": {"type": "string", "enum": ["APPROVED", "REJECTED", "MANUAL_REVIEW"]},
                "actor":      {"type": "string"},
                "note":       {"type": "string"},
            },
            "required": ["claim_id", "new_status", "actor", "note"],
        },
    ),
    types.Tool(
        name="create_claim",
        description=(
            "Persist a new claim row into the database with AWAITING_INPUT status. "
            "WRITE-RESTRICTED — called only by the orchestrator after agent reasoning."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id":                {"type": "integer"},
                "policy_id":              {"type": "integer"},
                "incident_date":          {"type": "string"},
                "incident_description":   {"type": "string"},
                "incident_location":      {"type": ["string", "null"]},
                "severity_label":         {"type": "string", "enum": ["LOW", "HIGH", "TOTAL_LOSS"]},
                "reported_damage_amount": {"type": ["string", "number", "null"]},
                "claim_status":           {
                    "type": "string",
                    "enum": ["APPROVED", "REJECTED", "MANUAL_REVIEW", "AWAITING_INPUT"],
                },
                "ai_reasoning":           {"type": "string"},
            },
            "required": [
                "user_id", "policy_id", "incident_date",
                "incident_description", "severity_label",
                "claim_status", "ai_reasoning",
            ],
        },
    ),
    types.Tool(
        name="append_reasoning",
        description=(
            "Append a text block to an existing claim's ai_reasoning column. "
            "Append-only — never overwrites. WRITE-RESTRICTED."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id":       {"type": "integer"},
                "text_to_append": {"type": "string"},
            },
            "required": ["claim_id", "text_to_append"],
        },
    ),
]


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    logger.info(f"call_tool | name={name} arguments_keys={list(arguments.keys())}")
    try:
        if name == "create_claim":
            result = await create_claim(arguments)
        elif name == "get_claim_by_id":
            result = await get_claim_by_id(int(arguments["claim_id"]))
        elif name == "list_claims":
            result = await list_claims(
                status=arguments.get("status"),
                limit=int(arguments.get("limit", 100)),
                offset=int(arguments.get("offset", 0)),
            )
        elif name == "update_claim_status":
            result = await update_claim_status(
                claim_id=int(arguments["claim_id"]),
                new_status=str(arguments["new_status"]),
                actor=str(arguments["actor"]),
                note=str(arguments["note"]),
            )
        elif name == "append_reasoning":
            result = await append_reasoning(
                claim_id=int(arguments["claim_id"]),
                text_to_append=str(arguments["text_to_append"]),
            )
        else:
            raise ValueError(f"Unknown tool: '{name}'")

        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, default=str),
        )]

    except ValueError as e:
        # Business error — user-facing, safe to surface verbatim.
        # Fix C: "kind": "business" so the orchestrator routes to ValueError.
        logger.warning(f"call_tool business error | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e), "kind": "business"}),
        )]
    except RuntimeError as e:
        # Internal error — DB / infra failure. Log the detail internally;
        # Fix C: "kind": "internal" so the orchestrator raises MCPClientError
        # and never surfaces the raw message to the HTTP client.
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
        Route("/sse", endpoint=handle_sse),
        Mount("/messages", app=sse_transport.handle_post_message),
    ],
)

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8002"))
    logger.info(f"Claims MCP server starting | transport=SSE host={host} port={port}")
    uvicorn.run(starlette_app, host=host, port=port)
