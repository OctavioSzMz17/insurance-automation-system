"""LangChain @tool wrappers for the Documents MCP server.

Thin async wrappers that expose the documents MCP functions to LangGraph
agents. Phase B uses direct in-process calls (via the importlib loader);
Phase C swaps to SSE clients.

Rules applied:
    Rule 3  — Read-only domain (PDF parsing)
    Rule 11 — Type hints; structured outputs returned as JSON-safe dicts
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from ._loader import load_mcp_tool

logger = logging.getLogger(__name__)

# Bind the underlying MCP tool function once at import time
_extract_claim_from_pdf = load_mcp_tool("documents", "extract_claim_from_pdf")


@tool
async def extract_claim_from_pdf(pdf_path: str) -> dict[str, Any]:
    """Parse an insurance claim PDF and extract structured fields.

    Returns a dict with: claim_id, user_id, insured_name, incident_date,
    description, location, damage_summary, and reported_damage_amount.

    Use this as the FIRST tool to call when given a pdf_path. The user_id
    in the returned dict feeds into get_user_by_id.

    Args:
        pdf_path: Absolute filesystem path to the PDF file.
    """
    logger.info(f"@tool extract_claim_from_pdf | pdf_path={pdf_path}")
    return await _extract_claim_from_pdf(pdf_path)


DOCUMENTS_TOOLS = [extract_claim_from_pdf]
