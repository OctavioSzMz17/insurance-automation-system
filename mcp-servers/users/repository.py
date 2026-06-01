"""Read-only repository for the users MCP."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from _common.db import get_engine
from packages.common.exceptions import NotFoundError

_COLUMNS = """
    id::text AS id,
    email,
    full_name,
    created_at,
    updated_at
"""

# This MCP serves INSURED CUSTOMERS (the `clients` table), not system staff.
# _COLUMNS is a server-side constant; user input is always bound via :param, never interpolated.
_SELECT_BY_ID = text(f"SELECT {_COLUMNS} FROM clients WHERE id = :user_id")  # nosec B608
_SELECT_BY_EMAIL = text(f"SELECT {_COLUMNS} FROM clients WHERE email = :email")  # nosec B608


async def fetch_user_by_id(dsn: str, user_id: UUID) -> dict[str, Any]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (await conn.execute(_SELECT_BY_ID, {"user_id": str(user_id)})).mappings().first()
    if row is None:
        raise NotFoundError("user not found")
    return dict(row)


async def fetch_user_by_email(dsn: str, email: str) -> dict[str, Any]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (await conn.execute(_SELECT_BY_EMAIL, {"email": email})).mappings().first()
    if row is None:
        raise NotFoundError("user not found")
    return dict(row)


# Directory listing — ADMIN-gated at the tool layer (see tools.py). Returns a
# minimal projection so an operator can pick the claimant a claim is filed for.
_SELECT_ALL_CLIENTS = text(
    "SELECT id::text AS id, email, full_name FROM clients ORDER BY full_name"
)  # nosec B608


async def list_clients(dsn: str) -> list[dict[str, Any]]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        rs = await conn.execute(_SELECT_ALL_CLIENTS)
        return [dict(r) for r in rs.mappings().all()]
