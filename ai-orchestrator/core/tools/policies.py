"""Typed wrappers for the policies MCP."""
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
    domain="policies",
    mcp_tool="get_policy_by_id",
    description="Fetch a single policy by id. ANALYST may only read policies it owns; ADMIN reads any.",
    input_schema=_ctx_schema({"policy_id": {"type": "string"}}, ["policy_id"]),
)
async def get_policy_by_id(pool: Any, *, policy_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("policies", "get_policy_by_id", {
        "policy_id": str(policy_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="policies",
    mcp_tool="get_policies_by_user_id",
    description="List all policies for a user. ANALYST may only target itself; ADMIN may target anyone.",
    input_schema=_ctx_schema({"user_id": {"type": "string"}}, ["user_id"]),
)
async def get_policies_by_user_id(pool: Any, *, user_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("policies", "get_policies_by_user_id", {
        "user_id": str(user_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="policies",
    mcp_tool="get_coverage_rules",
    description="Fetch the coverage_rules JSON for a policy. Same ACL as get_policy_by_id.",
    input_schema=_ctx_schema({"policy_id": {"type": "string"}}, ["policy_id"]),
)
async def get_coverage_rules(pool: Any, *, policy_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("policies", "get_coverage_rules", {
        "policy_id": str(policy_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })
