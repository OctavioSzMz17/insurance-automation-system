"""Users MCP tool handlers (read-only, ACL anti-IDOR).

ACL model (Rule 7):
- ADMIN: reads any user.
- ANALYST: reads ONLY itself. `caller_sub == user.email` is the gate; otherwise -> forbidden.
- The error message is generic ('forbidden' or 'user not found' depending on the path) and
  must NEVER reveal whether the target id/email actually exists, to prevent enumeration.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from _common.acl import assert_admin_or_self_user, is_admin
from _common.server_wiring import ToolSpec
from _common.settings import MCPSettings
from packages.common.exceptions import ForbiddenError

from . import repository as repo


class _GetUserByIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _GetUserByEmailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _ListClientsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def build_tools(settings: MCPSettings) -> list[ToolSpec]:
    dsn = settings.database_url

    async def get_user_by_id(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetUserByIdArgs.model_validate(args)
        # ANALYST anti-enumeration: short-circuit BEFORE the DB query if the caller is
        # asking for someone other than itself by id. We do not yet know the email of the
        # target id, so the only safe answer is 'forbidden' — and we must not let the
        # absence of the row become a side channel that leaks existence to an attacker.
        if not is_admin(parsed.caller_role):
            user = await repo.fetch_user_by_id(dsn, parsed.user_id)
            if user["email"] != parsed.caller_sub:
                raise ForbiddenError("forbidden")
            return {"user": user}
        user = await repo.fetch_user_by_id(dsn, parsed.user_id)
        return {"user": user}

    async def get_user_by_email(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetUserByEmailArgs.model_validate(args)
        # Email-based lookup: ANALYST may only resolve its own email. Compare BEFORE the
        # DB query so a non-self email returns 'forbidden' regardless of whether it exists,
        # closing the enumeration side channel (Rule 7).
        assert_admin_or_self_user(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            target_sub=parsed.email,
        )
        user = await repo.fetch_user_by_email(dsn, parsed.email)
        return {"user": user}

    async def list_clients(args: dict[str, Any]) -> dict[str, Any]:
        # Directory listing is a privileged, ADMIN-only operation: an operator
        # filing a claim on behalf of a client needs to choose the claimant.
        # ANALYST is intentionally denied (anti-IDOR Rule 7).
        parsed = _ListClientsArgs.model_validate(args)
        if not is_admin(parsed.caller_role):
            raise ForbiddenError("forbidden")
        clients = await repo.list_clients(dsn)
        return {"clients": clients}

    return [
        ToolSpec(
            name="get_user_by_id",
            description=(
                "Fetch a user by id. ANALYST may only fetch itself (compares user.email to caller_sub). "
                "ADMIN reads any user."
            ),
            input_schema=_schema(_GetUserByIdArgs),
            handler=get_user_by_id,
        ),
        ToolSpec(
            name="get_user_by_email",
            description=(
                "Fetch a user by email. ANALYST may only resolve its own email. ADMIN may resolve any."
            ),
            input_schema=_schema(_GetUserByEmailArgs),
            handler=get_user_by_email,
        ),
        ToolSpec(
            name="list_clients",
            description=(
                "List insured clients (id, email, full_name). ADMIN only — used by an "
                "operator to choose the claimant a claim is filed on behalf of."
            ),
            input_schema=_schema(_ListClientsArgs),
            handler=list_clients,
        ),
    ]
