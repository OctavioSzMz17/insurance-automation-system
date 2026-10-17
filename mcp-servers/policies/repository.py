"""Read-only repository for the policies MCP.

ACL note: a Policy is owned by a User. To enforce the row-level ACL inside the tool layer,
the repository JOINs policies → users to surface `owner_email`, which the ACL layer compares
against `caller_sub`. This avoids leaking unrelated policy data to non-owners (Rule 7).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from _common.db import get_engine
from packages.common.exceptions import NotFoundError

# A policy is owned by a CLIENT (insured customer). The client_id column keeps
# the output alias `user_id` so the orchestrator tool contract is unchanged.
_SELECT_POLICY_WITH_OWNER = text(
    """
    SELECT p.id::text                AS id,
           p.client_id::text         AS user_id,
           c.email                   AS owner_email,
           p.status,
           p.deductible_usd,
           p.coverage_limit_usd,
           p.effective_from,
           p.effective_to,
           p.coverage_rules,
           p.created_at,
           p.updated_at
    FROM policies p
    JOIN clients c ON c.id = p.client_id
    WHERE p.id = :policy_id
    """
)

_SELECT_POLICIES_BY_USER = text(
    """
    SELECT p.id::text                AS id,
           p.client_id::text         AS user_id,
           c.email                   AS owner_email,
           p.status,
           p.deductible_usd,
           p.coverage_limit_usd,
           p.effective_from,
           p.effective_to,
           p.coverage_rules,
           p.created_at,
           p.updated_at
    FROM policies p
    JOIN clients c ON c.id = p.client_id
    WHERE p.client_id = :user_id
    ORDER BY p.created_at DESC
    """
)

_SELECT_USER_EMAIL = text("SELECT email FROM clients WHERE id = :user_id")


async def fetch_policy_with_owner(dsn: str, policy_id: UUID) -> dict[str, Any]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (
            await conn.execute(_SELECT_POLICY_WITH_OWNER, {"policy_id": str(policy_id)})
        ).mappings().first()
    if row is None:
        raise NotFoundError("policy not found")
    return dict(row)


async def fetch_user_email(dsn: str, user_id: UUID) -> str:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (
            await conn.execute(_SELECT_USER_EMAIL, {"user_id": str(user_id)})
        ).mappings().first()
    if row is None:
        raise NotFoundError("user not found")
    return str(row["email"])


async def fetch_policies_by_user(dsn: str, user_id: UUID) -> list[dict[str, Any]]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        rs = await conn.execute(_SELECT_POLICIES_BY_USER, {"user_id": str(user_id)})
        return [dict(r) for r in rs.mappings().all()]
