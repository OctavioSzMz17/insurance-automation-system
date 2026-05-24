"""LangChain @tool wrapper for the Documents MCP server.

Fix 1 (Rule 7): calls the documents MCP over SSE instead of in-process.

Rules applied:
    Rule 3  — Read-only domain (PDF parsing)
    Rule 7  — Out-of-process SSE call; no in-process imports
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from ..mcp_client import call_mcp_tool

logger = logging.getLogger(__name__)


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
    from config import get_settings
    logger.info(f"@tool extract_claim_from_pdf | pdf_path={pdf_path}")
    settings = get_settings()
    return await call_mcp_tool(
        settings.documents_mcp_url,
        "extract_claim_from_pdf",
        {"pdf_path": pdf_path},
    )


DOCUMENTS_TOOLS = [extract_claim_from_pdf]
