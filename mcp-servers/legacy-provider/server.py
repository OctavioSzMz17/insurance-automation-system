"""Legacy Provider MCP Server — A3.4

Exposes legacy insurance data (users, policies) as MCP tools so that
AI agents can retrieve policy and user information without ever
touching the database directly (Rule 2).

Tools:
    get_user_by_id            — Fetch one user record by integer ID
    get_policies_by_user_id   — Fetch all policies belonging to a user

Transport:
    stdio  — default (Claude Desktop / local dev)
    SSE    — production (Rule 7: MCP ↔ orchestrator via SSE only)

Usage:
    python -m mcp-servers.legacy-provider.server

Rules applied:
    Rule 2  — Agents never query the DB directly; all access via MCP
    Rule 3  — This MCP owns the legacy-data domain only; read-only
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — Structured logging; no print() statements
    Rule 11 — Type hints on every function
"""

import asyncio
import json
import logging
import logging.config
import sys
from typing import Any

import sys
from pathlib import Path

# Allow running directly (Claude Desktop / local test) without package context
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))              # exposes db.py, tools/
sys.path.insert(0, str(_HERE.parents[1]))   # exposes packages/common

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from tools.get_user_by_id import get_user_by_id
from tools.get_policies_by_user_id import get_policies_by_user_id

# ------------------------------------------------------------------ #
# Logging (Rule 9 — no print() anywhere)                             #
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,  # keep stdout clean for MCP stdio protocol
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Server instance                                                     #
# ------------------------------------------------------------------ #
server: Server = Server("legacy-provider")

# ------------------------------------------------------------------ #
# Tool list                                                           #
# ------------------------------------------------------------------ #
TOOLS: list[types.Tool] = [
    types.Tool(
        name="get_user_by_id",
        description=(
            "Fetch a single user record from the legacy insurance database "
            "by their integer user_id. Returns user_id, name, and email."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "The user's primary key (positive integer)."
                }
            },
            "required": ["user_id"]
        }
    ),
    types.Tool(
        name="get_policies_by_user_id",
        description=(
            "Fetch all insurance policies belonging to a user. "
            "Returns a list of policy records (may be empty). "
            "Each record includes policy_id, type, status, deductible_usd, "
            "coverage_limit_usd, start_date, and end_date."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "The user's primary key (positive integer)."
                }
            },
            "required": ["user_id"]
        }
    ),
]


# ------------------------------------------------------------------ #
# Handler: list_tools                                                 #
# ------------------------------------------------------------------ #
@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the tools this server exposes.

    Called by the MCP client on connection to discover available tools.

    Returns:
        List of Tool definitions with name, description, and inputSchema.
    """
    logger.info("list_tools called")
    return TOOLS


# ------------------------------------------------------------------ #
# Handler: call_tool                                                  #
# ------------------------------------------------------------------ #
@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """Route an incoming tool call to the correct implementation.

    Args:
        name:      Tool name requested by the agent.
        arguments: Dict of input arguments validated against inputSchema.

    Returns:
        List containing a single TextContent with JSON-serialised result.

    Raises:
        ValueError: If the tool name is not recognised.
    """
    logger.info(f"call_tool | name={name} arguments={arguments}")

    try:
        if name == "get_user_by_id":
            user_id = int(arguments["user_id"])
            result  = await get_user_by_id(user_id)

        elif name == "get_policies_by_user_id":
            user_id = int(arguments["user_id"])
            result  = await get_policies_by_user_id(user_id)

        else:
            raise ValueError(f"Unknown tool: '{name}'")

        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False),
        )]

    except ValueError as e:
        # User not found or unknown tool — return as structured error text
        logger.warning(f"call_tool ValueError | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e)}),
        )]

    except RuntimeError as e:
        # DB connection / query failure
        logger.error(f"call_tool RuntimeError | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e)}),
        )]


# ------------------------------------------------------------------ #
# Entry point                                                         #
# ------------------------------------------------------------------ #
async def main() -> None:
    """Start the Legacy Provider MCP server on stdio transport."""
    logger.info("Legacy Provider MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
