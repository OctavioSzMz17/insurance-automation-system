"""LangChain @tool wrappers for user/policy MCP read services.

Fix 1 (Rule 7): calls users and policies MCPs over SSE instead of in-process.

Rules applied:
    Rule 2  — Read-only DB access; monetary fields stay as strings
    Rule 3  — Read-only domain (legacy data)
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
async def get_user_by_id(user_id: int) -> dict[str, Any]:
    """Fetch a user record from the legacy insurance database by integer ID.

    Returns a dict with: user_id, name, email.
    Raises a ValueError if the user does not exist — caller should handle.

    Use this AFTER extract_claim_from_pdf to verify the policyholder exists.

    Args:
        user_id: Integer primary key of the policyholder.
    """
    from config import get_settings
    logger.info(f"@tool get_user_by_id | user_id={user_id}")
    settings = get_settings()
    return await call_mcp_tool(
        settings.users_mcp_url,
        "get_user_by_id",
        {"user_id": user_id},
    )


@tool
async def get_policies_by_user_id(user_id: int) -> list[dict[str, Any]]:
    """Fetch all insurance policies belonging to a user.

    Returns a list of policy dicts (may be empty). Each policy has:
    policy_id, user_id, type, status, deductible_usd (str),
    coverage_limit_usd (str), start_date (ISO), end_date (ISO).

    Use this AFTER confirming the user exists. Monetary fields are returned
    as strings to preserve Decimal precision.

    Args:
        user_id: Integer primary key of the policyholder.
    """
    from config import get_settings
    logger.info(f"@tool get_policies_by_user_id | user_id={user_id}")
    settings = get_settings()
    return await call_mcp_tool(
        settings.policies_mcp_url,
        "get_policies_by_user_id",
        {"user_id": user_id},
    )


LEGACY_TOOLS = [get_user_by_id, get_policies_by_user_id]
