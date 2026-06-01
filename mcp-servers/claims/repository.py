"""Read-only repository for the claims MCP.

All SQL uses parameterized statements (text(:param)) — never f-string interpolation. The
async engine singleton from `_common.db` is reused across all tool calls in the process.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from _common.db import get_engine
from packages.common.exceptions import NotFoundError


_SELECT_CLAIM = text(
    """
    SELECT id::text AS id,
           submitted_by_sub,
           claimant_sub,
           policy_id::text AS policy_id,
           pdf_sha256,
           reported_damage_amount,
           severity,
           status,
           review_flags,
           evidence_quotes,
           ai_summary,
           decision_metadata,
           created_at,
           updated_at
    FROM claims
    WHERE id = :claim_id
    """
)

_SELECT_CLAIMS_BY_SUBMITTER = text(
    """
    SELECT id::text AS id,
           submitted_by_sub,
           claimant_sub,
           policy_id::text AS policy_id,
           pdf_sha256,
           reported_damage_amount,
           severity,
           status,
           review_flags,
           evidence_quotes,
           ai_summary,
           created_at,
           updated_at
    FROM claims
    WHERE submitted_by_sub = :submitter_sub
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
    """
)

_SELECT_CLAIMS_ALL = text(
    """
    SELECT id::text AS id,
           submitted_by_sub,
           claimant_sub,
           policy_id::text AS policy_id,
           pdf_sha256,
           reported_damage_amount,
           severity,
           status,
           review_flags,
           evidence_quotes,
           ai_summary,
           created_at,
           updated_at
    FROM claims
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
    """
)


async def fetch_claim_by_id(dsn: str, claim_id: UUID) -> dict[str, Any]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (await conn.execute(_SELECT_CLAIM, {"claim_id": str(claim_id)})).mappings().first()
    if row is None:
        raise NotFoundError("claim not found")
    return dict(row)


async def fetch_claims_by_submitter(
    dsn: str, *, submitter_sub: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        if submitter_sub is None or submitter_sub == "*":
            rs = await conn.execute(_SELECT_CLAIMS_ALL, {"limit": limit, "offset": offset})
        else:
            rs = await conn.execute(
                _SELECT_CLAIMS_BY_SUBMITTER,
                {"submitter_sub": submitter_sub, "limit": limit, "offset": offset},
            )
        return [dict(r) for r in rs.mappings().all()]


async def fetch_claim_trace(dsn: str, claim_id: UUID) -> dict[str, Any]:
    """Trace is the claim row + its decision_metadata JSONB (Rule 2)."""
    claim = await fetch_claim_by_id(dsn, claim_id)
    return {
        "claim_id": claim["id"],
        "status": claim["status"],
        "severity": claim["severity"],
        "submitted_by_sub": claim["submitted_by_sub"],
        "decision_metadata": claim["decision_metadata"],
        "created_at": claim["created_at"],
        "updated_at": claim["updated_at"],
    }


# ── Write side (used by @write_restricted tools only; never during agent runtime) ──


_INSERT_CLAIM = text(
    """
    INSERT INTO claims (
        submitted_by_sub, claimant_sub, policy_id, pdf_sha256,
        reported_damage_amount, severity, status, decision_metadata,
        review_flags, evidence_quotes, ai_summary
    ) VALUES (
        :submitted_by_sub, :claimant_sub, :policy_id, :pdf_sha256,
        :reported_damage_amount, :severity, :status, CAST(:decision_metadata AS JSONB),
        CAST(:review_flags AS JSONB), CAST(:evidence_quotes AS JSONB), :ai_summary
    )
    ON CONFLICT (submitted_by_sub, pdf_sha256) DO NOTHING
    RETURNING id::text AS id, status, created_at, updated_at
    """
)


_UPDATE_STATUS = text(
    """
    UPDATE claims
       SET status = :new_status,
           decision_metadata = COALESCE(decision_metadata, '{}'::jsonb)
                               || CAST(:decision_metadata AS JSONB)
     WHERE id = :claim_id
       AND status <> 'MANUAL_REVIEW'   -- Rule 5: MANUAL_REVIEW is terminal
    RETURNING id::text AS id, status, updated_at
    """
)


_SELECT_FOR_DEDUP = text(
    "SELECT id::text AS id, status FROM claims "
    "WHERE submitted_by_sub = :submitted_by_sub AND pdf_sha256 = :pdf_sha256"
)


async def insert_claim(
    dsn: str,
    *,
    submitted_by_sub: str,
    policy_id: UUID,
    pdf_sha256: str,
    reported_damage_amount: str,
    severity: str,
    status: str,
    decision_metadata_json: str,
    claimant_sub: str | None = None,
    review_flags_json: str = "[]",
    evidence_quotes_json: str = "[]",
    ai_summary: str | None = None,
) -> dict[str, Any]:
    """Insert a new claim. Returns the row, or the existing one if dedup-collision (Rule 13)."""
    import json
    engine = await get_engine(dsn)
    async with engine.begin() as conn:
        rs = await conn.execute(
            _INSERT_CLAIM,
            {
                "submitted_by_sub": submitted_by_sub,
                "claimant_sub": claimant_sub,
                "policy_id": str(policy_id),
                "pdf_sha256": pdf_sha256,
                "reported_damage_amount": reported_damage_amount,
                "severity": severity,
                "status": status,
                "decision_metadata": decision_metadata_json,
                "review_flags": review_flags_json,
                "evidence_quotes": evidence_quotes_json,
                "ai_summary": ai_summary,
            },
        )
        row = rs.mappings().first()
        if row is not None:
            return {"created": True, **dict(row)}
        # ON CONFLICT DO NOTHING → fetch existing row for the dedup answer.
        existing = (
            await conn.execute(
                _SELECT_FOR_DEDUP,
                {"submitted_by_sub": submitted_by_sub, "pdf_sha256": pdf_sha256},
            )
        ).mappings().first()
        if existing is None:
            raise NotFoundError("claim could not be inserted nor located")
        return {"created": False, **dict(existing)}


async def update_status(
    dsn: str,
    *,
    claim_id: UUID,
    new_status: str,
    decision_metadata_json: str,
) -> dict[str, Any]:
    """Update status + merge decision_metadata. Returns the updated row or raises NotFound.

    Rule 5: a claim already in MANUAL_REVIEW cannot transition. The WHERE clause enforces this
    at the DB layer; the application sees an empty RETURNING and raises NotFound, which the
    tool surfaces as a business error — never a silent no-op.
    """
    engine = await get_engine(dsn)
    async with engine.begin() as conn:
        rs = await conn.execute(
            _UPDATE_STATUS,
            {
                "claim_id": str(claim_id),
                "new_status": new_status,
                "decision_metadata": decision_metadata_json,
            },
        )
        row = rs.mappings().first()
    if row is None:
        raise NotFoundError("claim not updatable: not found or in terminal state")
    return dict(row)


async def fetch_status_only(dsn: str, claim_id: UUID) -> str:
    engine = await get_engine(dsn)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM claims WHERE id = :claim_id"),
                {"claim_id": str(claim_id)},
            )
        ).mappings().first()
    if row is None:
        raise NotFoundError("claim not found")
    return str(row["status"])
