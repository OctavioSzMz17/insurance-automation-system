"""Documents MCP Server — B1.3

Exposes PDF extraction tools so that AI agents can parse unstructured
claim documents (PDFs) into structured data without leaving the MCP
boundary (Rule 3).

Tools:
    extract_claim_from_pdf — Parse a PDF claim report into a structured dict

Transport:
    stdio  — default (Claude Desktop / local dev)
    SSE    — production (Rule 7: MCP ↔ orchestrator via SSE only)

Usage:
    py mcp-servers/documents/server.py

Rules applied:
    Rule 3  — This MCP owns the documents domain only; read-only (pure parsing)
    Rule 9  — Structured logging; no print() statements
    Rule 11 — Type hints on every function
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow running directly (Claude Desktop / local test) without package context
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))              # exposes tools/
sys.path.insert(0, str(_HERE.parents[1]))   # exposes packages/common

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from tools.extract_claim_from_pdf import extract_claim_from_pdf

# ------------------------------------------------------------------ #
# Logging (Rule 9 — no print() anywhere; stderr keeps stdout clean) #
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
server: Server = Server("documents")

# ------------------------------------------------------------------ #
# Tool list                                                           #
# ------------------------------------------------------------------ #
TOOLS: list[types.Tool] = [
    types.Tool(
        name="extract_claim_from_pdf",
        description=(
            "Parse a PDF claim report and extract structured claim data. "
            "Returns a dict with claim_id, user_id, insured_name, "
            "incident_date, description, location, damage_summary, and "
            "reported_damage_amount (may be null). Used by the analyst_agent "
            "to triage incoming claims."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": "Absolute filesystem path to the PDF file to parse."
                }
            },
            "required": ["pdf_path"]
        }
    ),
]


# ------------------------------------------------------------------ #
# Handler: list_tools                                                 #
# ------------------------------------------------------------------ #
@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the tools this server exposes."""
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
        List containing a single TextContent with JSON-serialised result
        or a structured error dict.
    """
    logger.info(f"call_tool | name={name} arguments={arguments}")

    try:
        if name == "extract_claim_from_pdf":
            pdf_path = str(arguments["pdf_path"])
            result   = await extract_claim_from_pdf(pdf_path)
        else:
            raise ValueError(f"Unknown tool: '{name}'")

        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False),
        )]

    except FileNotFoundError as e:
        logger.warning(f"call_tool FileNotFoundError | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e)}, ensure_ascii=False),
        )]

    except ValueError as e:
        logger.warning(f"call_tool ValueError | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": str(e)}, ensure_ascii=False),
        )]

    except Exception as e:
        logger.error(f"call_tool unexpected error | tool={name} error={e}")
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": f"Unexpected error: {e}"}, ensure_ascii=False),
        )]


# ------------------------------------------------------------------ #
# Entry point                                                         #
# ------------------------------------------------------------------ #
async def main() -> None:
    """Start the Documents MCP server on stdio transport."""
    logger.info("Documents MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
