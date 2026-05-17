"""Claims MCP Server — B2.3

Exposes write-restricted claim tools to the auditor_agent. Owns the
claim-write domain (Rule 3).

Tools:
    create_claim — @write_restricted, persists a ClaimDecision

Transport:
    stdio  — default (Claude Desktop / local dev)
    SSE    — production (Rule 7: MCP ↔ orchestrator via SSE only)

Rules applied:
    Rule 3  — Domain isolation; only WRITE-capable agents may call these tools
    Rule 7  — Role-based access enforced at the MCP router (C1.5)
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — Structured logging; no print() statements
    Rule 11 — Type hints on every function
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow direct execution by Claude Desktop / Python CLI
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))              # exposes db.py, tools/
sys.path.insert(0, str(_HERE.parents[1]))   # exposes packages/common

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from tools.create_claim        import create_claim
from tools.get_claim_by_id     import get_claim_by_id
from tools.update_claim_status import update_claim_status

# ------------------------------------------------------------------ #
# Logging (stderr keeps stdout clean for MCP protocol)               #
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Server instance                                                     #
# ------------------------------------------------------------------ #
server: Server = Server("claims")

TOOLS: list[types.Tool] = [
    types.Tool(
        name="get_claim_by_id",
        description=(
            "Fetch a single claim record by its integer claim_id. "
            "Read-only; returns every column from the claims row."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "integer",
                    "description": "Primary key of the claim to retrieve.",
                }
            },
            "required": ["claim_id"],
        },
    ),
    types.Tool(
        name="update_claim_status",
        description=(
            "Manually move a claim from MANUAL_REVIEW/AWAITING_INPUT to "
            "APPROVED or REJECTED. WRITE-RESTRICTED. Appends an audit "
            "line to ai_reasoning."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "claim_id":   {"type": "integer"},
                "new_status": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
                "actor":      {"type": "string"},
                "note":       {"type": "string"},
            },
            "required": ["claim_id", "new_status", "actor", "note"],
        },
    ),
    types.Tool(
        name="create_claim",
        description=(
            "Persist an auditor-produced claim decision into the database. "
            "WRITE-RESTRICTED — only callable by agents with WRITE role. "
            "Returns the persisted claim row including the assigned claim_id."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id":              {"type": "integer"},
                "policy_id":            {"type": "integer"},
                "incident_date":        {"type": "string", "description": "ISO-8601 date"},
                "incident_description": {"type": "string"},
                "incident_location":    {"type": ["string", "null"]},
                "severity_label":       {"type": "string", "enum": ["LOW", "HIGH", "TOTAL_LOSS"]},
                "reported_damage_amount": {"type": ["string", "number", "null"]},
                "claim_status":         {"type": "string", "enum": ["APPROVED", "REJECTED", "MANUAL_REVIEW", "AWAITING_INPUT"]},
                "ai_reasoning":         {"type": "string"},
            },
            "required": [
                "user_id", "policy_id", "incident_date",
                "incident_description", "severity_label",
                "claim_status", "ai_reasoning",
            ],
        },
    ),
]


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    logger.info("list_tools called")
    return TOOLS


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    logger.info(f"call_tool | name={name} arguments_keys={list(arguments.keys())}")
    try:
        if name == "create_claim":
            result = await create_claim(arguments)
        elif name == "get_claim_by_id":
            result = await get_claim_by_id(int(arguments["claim_id"]))
        elif name == "update_claim_status":
            result = await update_claim_status(
                claim_id=int(arguments["claim_id"]),
                new_status=str(arguments["new_status"]),
                actor=str(arguments["actor"]),
                note=str(arguments["note"]),
            )
        else:
            raise ValueError(f"Unknown tool: '{name}'")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    except ValueError as e:
        logger.warning(f"call_tool ValueError | tool={name} error={e}")
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

    except RuntimeError as e:
        logger.error(f"call_tool RuntimeError | tool={name} error={e}")
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main() -> None:
    logger.info("Claims MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
