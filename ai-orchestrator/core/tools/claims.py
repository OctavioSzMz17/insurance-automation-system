"""Typed wrappers for the claims MCP."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from ._spec import mcp_tool


def _ctx_schema(extra: dict[str, Any] | None = None, required_extra: list[str] | None = None) -> dict[str, Any]:
    props: dict[str, dict[str, Any]] = {
        "caller_sub": {"type": "string"},
        "caller_role": {"type": "string"},
    }
    if extra:
        props.update(extra)
    required = ["caller_sub", "caller_role"] + (required_extra or [])
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


@mcp_tool(
    domain="claims",
    mcp_tool="get_claim_by_id",
    description="Fetch a single claim by id. ANALYST may only fetch its own; ADMIN reads any.",
    input_schema=_ctx_schema({"claim_id": {"type": "string"}}, required_extra=["claim_id"]),
)
async def get_claim_by_id(pool: Any, *, claim_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("claims", "get_claim_by_id", {
        "claim_id": str(claim_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


@mcp_tool(
    domain="claims",
    mcp_tool="list_claims_by_submitter",
    description=(
        "List claims for a submitter. ANALYST role forces submitter_sub to caller_sub; "
        "ADMIN may pass any submitter_sub or omit for all."
    ),
    input_schema=_ctx_schema({
        "submitter_sub": {"type": "string"},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
    }),
)
async def list_claims_by_submitter(
    pool: Any, *, caller_sub: str, caller_role: str,
    submitter_sub: str | None = None, limit: int = 20, offset: int = 0,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "caller_sub": caller_sub, "caller_role": caller_role, "limit": limit, "offset": offset,
    }
    if submitter_sub is not None:
        args["submitter_sub"] = submitter_sub
    return await pool.call("claims", "list_claims_by_submitter", args)


@mcp_tool(
    domain="claims",
    mcp_tool="get_claim_trace",
    description="Fetch claim audit trace (decision_metadata + timestamps). Same ACL as get_claim_by_id.",
    input_schema=_ctx_schema({"claim_id": {"type": "string"}}, required_extra=["claim_id"]),
)
async def get_claim_trace(pool: Any, *, claim_id: UUID | str, caller_sub: str, caller_role: str) -> dict[str, Any]:
    return await pool.call("claims", "get_claim_trace", {
        "claim_id": str(claim_id), "caller_sub": caller_sub, "caller_role": caller_role,
    })


# ── Write tools (Phase 3). NEVER exposed to LLMs; only the mutation handler calls these. ──


@mcp_tool(
    domain="claims",
    mcp_tool="create_claim",
    description=(
        "@write_restricted — INSERT a claim. Only called by the mutation handler after human "
        "approval. Honors dedup UNIQUE(submitted_by_sub, pdf_sha256)."
    ),
    input_schema=_ctx_schema(
        {
            "submitted_by_sub": {"type": "string"},
            "policy_id": {"type": "string"},
            "pdf_sha256": {"type": "string"},
            "reported_damage_amount": {"type": "string"},
            "severity": {"type": "string"},
            "status": {"type": "string"},
            "decision_metadata": {"type": "object"},
            "claimant_sub": {"type": "string"},
            "review_flags": {"type": "array"},
            "evidence_quotes": {"type": "array"},
            "ai_summary": {"type": "string"},
        },
        required_extra=["submitted_by_sub", "policy_id", "pdf_sha256",
                        "reported_damage_amount", "severity"],
    ),
    write_restricted=True,
)
async def create_claim(
    pool: Any,
    *,
    submitted_by_sub: str,
    policy_id: UUID | str,
    pdf_sha256: str,
    reported_damage_amount: str,
    severity: str,
    status: str = "SUBMITTED",
    decision_metadata: dict[str, Any] | None = None,
    claimant_sub: str | None = None,
    review_flags: list[str] | None = None,
    evidence_quotes: list[str] | None = None,
    ai_summary: str | None = None,
    caller_sub: str,
    caller_role: str,
) -> dict[str, Any]:
    return await pool.call("claims", "create_claim", {
        "submitted_by_sub": submitted_by_sub,
        "claimant_sub": claimant_sub,
        "policy_id": str(policy_id),
        "pdf_sha256": pdf_sha256,
        "reported_damage_amount": reported_damage_amount,
        "severity": severity,
        "status": status,
        "decision_metadata": decision_metadata or {},
        "review_flags": review_flags or [],
        "evidence_quotes": evidence_quotes or [],
        "ai_summary": ai_summary,
        "caller_sub": caller_sub,
        "caller_role": caller_role,
    })


@mcp_tool(
    domain="claims",
    mcp_tool="update_claim_status",
    description=(
        "@write_restricted — transition a claim's status and merge decision_metadata. "
        "MANUAL_REVIEW is terminal (Rule 5). Refuses any transition out of a terminal status."
    ),
    input_schema=_ctx_schema(
        {
            "claim_id": {"type": "string"},
            "new_status": {"type": "string"},
            "decision_metadata": {"type": "object"},
        },
        required_extra=["claim_id", "new_status"],
    ),
    write_restricted=True,
)
async def update_claim_status(
    pool: Any,
    *,
    claim_id: UUID | str,
    new_status: str,
    decision_metadata: dict[str, Any] | None = None,
    caller_sub: str,
    caller_role: str,
) -> dict[str, Any]:
    return await pool.call("claims", "update_claim_status", {
        "claim_id": str(claim_id),
        "new_status": new_status,
        "decision_metadata": decision_metadata or {},
        "caller_sub": caller_sub,
        "caller_role": caller_role,
    })
