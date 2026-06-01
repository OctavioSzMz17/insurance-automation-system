"""Typed wrappers for the users MCP. No list_all_users tool — anti-IDOR (Rule 7)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from ._spec import mcp_tool


def _ctx_schema(extra: dict[str, Any], required_extra: list[str]) -> dict[str, Any]:
    props: dict[str, dict[str, Any]] = {
        "caller_sub": {"type": "string"},
        "caller_role": {"type": "string"},
        **extra,
    }
    required = ["caller_sub", "caller_role", *required_extra]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


@mcp_tool(
    domain="users",
    mcp_tool="get_user_by_id",
    description="Fetch a user by id. ANALYST may only fetch itself; ADMIN reads any.",
    input_schema=_ctx_schema({"user_id": {"type": "string"}}, ["user_id"]),
)
async def get_user_by_id(pool: Any, *, user_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("users", "get_user_by_id", {
        "user_id": str(user_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="users",
    mcp_tool="get_user_by_email",
    description="Fetch a user by email. ANALYST may only resolve its own email; ADMIN may resolve any.",
    input_schema=_ctx_schema({"email": {"type": "string"}}, ["email"]),
)
async def get_user_by_email(pool: Any, *, email: str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("users", "get_user_by_email", {
        "email": email, "caller_sub": caller_sub, "caller_role": caller_role,
    })
