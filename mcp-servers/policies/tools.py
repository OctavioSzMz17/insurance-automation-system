"""Policies MCP tool handlers (read-only, ACL-enforced).

ACL model (Rule 7):
- ADMIN: reads any policy or any user's policies.
- ANALYST: may only read policies that belong to itself (owner_email == caller_sub).
- The denial message is always the generic 'forbidden' — never echoes owner_email or user_id,
  to avoid leaking the existence/ownership of policies the caller does not own.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from _common.acl import assert_admin_or_self_user, is_admin
from _common.server_wiring import ToolSpec
from _common.settings import MCPSettings
from packages.common.exceptions import ForbiddenError

from . import repository as repo

# ── Input models ────────────────────────────────────────────────────────────


class _GetPolicyByIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: UUID
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _GetPoliciesByUserIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _GetCoverageRulesArgs(_GetPolicyByIdArgs):
    pass


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


# ── Handlers ────────────────────────────────────────────────────────────────


def build_tools(settings: MCPSettings) -> list[ToolSpec]:
    dsn = settings.database_url

    async def get_policy_by_id(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetPolicyByIdArgs.model_validate(args)
        row = await repo.fetch_policy_with_owner(dsn, parsed.policy_id)
        if not is_admin(parsed.caller_role) and row["owner_email"] != parsed.caller_sub:
            raise ForbiddenError("forbidden")
        # Strip owner_email from the response — it is an ACL anchor, not a payload field.
        row.pop("owner_email", None)
        return {"policy": row}

    async def get_policies_by_user_id(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetPoliciesByUserIdArgs.model_validate(args)
        owner_email = await repo.fetch_user_email(dsn, parsed.user_id)
        assert_admin_or_self_user(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            target_sub=owner_email,
        )
        rows = await repo.fetch_policies_by_user(dsn, parsed.user_id)
        for r in rows:
            r.pop("owner_email", None)
        return {"policies": rows}

    async def get_coverage_rules(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetCoverageRulesArgs.model_validate(args)
        row = await repo.fetch_policy_with_owner(dsn, parsed.policy_id)
        if not is_admin(parsed.caller_role) and row["owner_email"] != parsed.caller_sub:
            raise ForbiddenError("forbidden")
        return {
            "policy_id": row["id"],
            "coverage_rules": row["coverage_rules"],
        }

    return [
        ToolSpec(
            name="get_policy_by_id",
            description=(
                "Fetch a single policy by id. ANALYST may only read policies it owns; ADMIN reads any."
            ),
            input_schema=_schema(_GetPolicyByIdArgs),
            handler=get_policy_by_id,
        ),
        ToolSpec(
            name="get_policies_by_user_id",
            description=(
                "List all policies for a user. ANALYST may only target itself; ADMIN may target anyone."
            ),
            input_schema=_schema(_GetPoliciesByUserIdArgs),
            handler=get_policies_by_user_id,
        ),
        ToolSpec(
            name="get_coverage_rules",
            description="Fetch the coverage_rules JSON for a policy. Same ACL as get_policy_by_id.",
            input_schema=_schema(_GetCoverageRulesArgs),
            handler=get_coverage_rules,
        ),
    ]
