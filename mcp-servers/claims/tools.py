"""Claims MCP tool handlers (read-only, ACL-enforced).

Each handler:
1. Validates the input through a Pydantic model with extra="forbid", strict=True.
2. Loads the row (or list).
3. Enforces row-level ACL via `_common.acl` primitives.
4. Returns plain JSON-serializable dicts.

The `@classified` wrapper applied at registration time (`server_wiring`) turns any
ValueError/DomainError into `{kind:business}` and any other exception into the safe
`upstream_internal_error` (Rule 7). Tool code does not need its own try/except.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from _common.acl import (
    assert_can_read_claim,
    is_admin,
    resolve_submitter_filter,
)
from _common.server_wiring import ToolSpec
from _common.settings import MCPSettings
from _common.tool_security import write_restricted
from packages.common.exceptions import ForbiddenError

from . import repository as repo


_TERMINAL_STATUSES = frozenset({"APPROVED", "REJECTED", "MANUAL_REVIEW"})
_VALID_NEW_STATUSES = frozenset({
    "SUBMITTED", "AWAITING_INPUT", "UNDER_REVIEW", "APPROVED", "REJECTED", "MANUAL_REVIEW",
})


# ── Input models ────────────────────────────────────────────────────────────


class _GetClaimByIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: UUID
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _ListClaimsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)
    submitter_sub: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10_000)


class _GetClaimTraceArgs(_GetClaimByIdArgs):
    pass


# Write tool inputs ──────────────────────────────────────────────────────────


class _CreateClaimArgs(BaseModel):
    """Strict input — extra=forbid is the security boundary for write tools (Rule 4)."""

    model_config = ConfigDict(extra="forbid")

    submitted_by_sub: str = Field(min_length=1, max_length=255)
    claimant_sub: str | None = Field(default=None, max_length=255)
    policy_id: UUID
    pdf_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reported_damage_amount: str = Field(min_length=1, max_length=20)
    severity: str = Field(min_length=1, max_length=16)
    status: str = Field(default="SUBMITTED", min_length=1, max_length=32)
    decision_metadata: dict[str, Any] = Field(default_factory=dict)
    review_flags: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    ai_summary: str | None = Field(default=None, max_length=4000)
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


class _UpdateStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    new_status: str = Field(min_length=1, max_length=32)
    decision_metadata: dict[str, Any] = Field(default_factory=dict)
    caller_sub: str = Field(min_length=1, max_length=255)
    caller_role: str = Field(min_length=1, max_length=32)


# ── JSON schemas (advertised to clients via list_tools) ─────────────────────


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


# ── Handlers ────────────────────────────────────────────────────────────────


def build_tools(settings: MCPSettings) -> list[ToolSpec]:
    dsn = settings.database_url

    async def get_claim_by_id(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetClaimByIdArgs.model_validate(args)
        claim = await repo.fetch_claim_by_id(dsn, parsed.claim_id)
        assert_can_read_claim(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            submitted_by_sub=claim["submitted_by_sub"],
        )
        return {"claim": claim}

    async def list_claims_by_submitter(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _ListClaimsArgs.model_validate(args)
        effective_sub = resolve_submitter_filter(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            requested_sub=parsed.submitter_sub,
        )
        rows = await repo.fetch_claims_by_submitter(
            dsn,
            submitter_sub=effective_sub,
            limit=parsed.limit,
            offset=parsed.offset,
        )
        return {"claims": rows, "filtered_by_sub": effective_sub}

    async def get_claim_trace(args: dict[str, Any]) -> dict[str, Any]:
        parsed = _GetClaimTraceArgs.model_validate(args)
        claim = await repo.fetch_claim_by_id(dsn, parsed.claim_id)
        assert_can_read_claim(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            submitted_by_sub=claim["submitted_by_sub"],
        )
        trace = await repo.fetch_claim_trace(dsn, parsed.claim_id)
        return {"trace": trace}

    # ── write tools (Rule 4 + Rule 6: only callable when MCP_WRITE_ENABLED=1) ──

    @write_restricted
    async def create_claim(args: dict[str, Any]) -> dict[str, Any]:
        import json
        parsed = _CreateClaimArgs.model_validate(args)
        # ACL: ADMIN may insert for anyone; otherwise submitted_by_sub MUST equal caller_sub.
        if not is_admin(parsed.caller_role) and parsed.submitted_by_sub != parsed.caller_sub:
            raise ForbiddenError("forbidden")
        if parsed.status not in _VALID_NEW_STATUSES:
            raise ValueError("invalid status")
        row = await repo.insert_claim(
            dsn,
            submitted_by_sub=parsed.submitted_by_sub,
            claimant_sub=parsed.claimant_sub,
            policy_id=parsed.policy_id,
            pdf_sha256=parsed.pdf_sha256,
            reported_damage_amount=parsed.reported_damage_amount,
            severity=parsed.severity,
            status=parsed.status,
            decision_metadata_json=json.dumps(parsed.decision_metadata, default=str),
            review_flags_json=json.dumps(parsed.review_flags, default=str),
            evidence_quotes_json=json.dumps(parsed.evidence_quotes, default=str),
            ai_summary=parsed.ai_summary,
        )
        return {"claim": row}

    @write_restricted
    async def update_claim_status(args: dict[str, Any]) -> dict[str, Any]:
        import json
        parsed = _UpdateStatusArgs.model_validate(args)
        if parsed.new_status not in _VALID_NEW_STATUSES:
            raise ValueError("invalid new_status")
        # ACL: load row first, then verify caller is owner or admin.
        existing = await repo.fetch_claim_by_id(dsn, parsed.claim_id)
        assert_can_read_claim(
            caller_sub=parsed.caller_sub,
            caller_role=parsed.caller_role,
            submitted_by_sub=existing["submitted_by_sub"],
        )
        # Rule 5: MANUAL_REVIEW is terminal — even at the application layer (DB also enforces).
        if existing["status"] == "MANUAL_REVIEW":
            raise ForbiddenError("claim is in MANUAL_REVIEW; transition refused")
        if existing["status"] in _TERMINAL_STATUSES and parsed.new_status != existing["status"]:
            raise ForbiddenError("claim is in a terminal status; transition refused")
        row = await repo.update_status(
            dsn,
            claim_id=parsed.claim_id,
            new_status=parsed.new_status,
            decision_metadata_json=json.dumps(parsed.decision_metadata, default=str),
        )
        return {"claim": row}

    return [
        ToolSpec(
            name="get_claim_by_id",
            description="Fetch a single claim by id. ANALYST can only fetch its own; ADMIN reads any.",
            input_schema=_schema(_GetClaimByIdArgs),
            handler=get_claim_by_id,
        ),
        ToolSpec(
            name="list_claims_by_submitter",
            description=(
                "List claims for a submitter. ANALYST role forces submitter_sub = caller_sub "
                "(anti-enumeration). ADMIN may pass any submitter_sub or omit for all."
            ),
            input_schema=_schema(_ListClaimsArgs),
            handler=list_claims_by_submitter,
        ),
        ToolSpec(
            name="get_claim_trace",
            description="Fetch claim audit trace (decision_metadata + timestamps). Same ACL as get_claim_by_id.",
            input_schema=_schema(_GetClaimTraceArgs),
            handler=get_claim_trace,
        ),
        ToolSpec(
            name="create_claim",
            description=(
                "@write_restricted — INSERT a claim row. Only callable when MCP_WRITE_ENABLED=1. "
                "ACL: ADMIN or submitted_by_sub == caller_sub. Honors dedup UNIQUE(submitted_by_sub, pdf_sha256)."
            ),
            input_schema=_schema(_CreateClaimArgs),
            handler=create_claim,
        ),
        ToolSpec(
            name="update_claim_status",
            description=(
                "@write_restricted — UPDATE status + merge decision_metadata. "
                "MANUAL_REVIEW is terminal (Rule 5). Refuses any transition from a terminal status."
            ),
            input_schema=_schema(_UpdateStatusArgs),
            handler=update_claim_status,
        ),
    ]
