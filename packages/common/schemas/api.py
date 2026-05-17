"""HTTP request/response schemas for the orchestrator API (Phase C).

Rule 1 — All Pydantic models live in packages/common/.
Rule 11 — Strong typing everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── POST /claims/submit ──────────────────────────────────────────

class ClaimSubmitRequest(BaseModel):
    """Body for POST /claims/submit.

    Phase C C1.1 accepts only a pdf_path (orchestrator + MCP share a
    filesystem volume). Multipart upload (`UploadFile`) will be added
    when we deploy with the shared PVC (Phase B1.4 already provisions it).
    """
    pdf_path: str = Field(
        ...,
        description="Absolute filesystem path to the claim PDF.",
        min_length=1,
    )


class ClaimSubmitResponse(BaseModel):
    """Response from POST /claims/submit.

    After C1.5 (Rule 6), `recommended_decision` is the auditor's
    RECOMMENDATION — the persisted claim itself sits in
    claim_status="AWAITING_INPUT" until a human reviewer calls
    POST /claims/{id}/approve or /reject.
    """
    request_id:        str
    timestamp:         datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pipeline_status:   Literal["SUCCESS", "ANALYST_FAILED", "AUDITOR_FAILED"]
    recommended_decision: Optional[str] = None   # auditor's recommendation — NOT final
    claim_status:      Optional[str]   = None    # actual persisted status (e.g. AWAITING_INPUT)
    persisted_claim_id: Optional[int]  = None
    summary:           Optional[str]   = None
    triage_report:     Optional[dict[str, Any]] = None
    claim_decision:    Optional[dict[str, Any]] = None
    analyst_retries:   int = 0
    auditor_retries:   int = 0
    analyst_latency_ms: int = 0
    auditor_latency_ms: int = 0
    total_latency_ms:  int = 0
    error_message:     Optional[str] = None


# ── POST /claims/{claim_id}/approve ──────────────────────────────

class ClaimApproveRequest(BaseModel):
    """Body for POST /claims/{claim_id}/approve (Rule 6 user confirmation).

    Approver identity comes from the JWT `sub` claim (C1.4) — no body
    field, no impersonation. `reason` is appended to ai_reasoning so
    the chain-of-thought stays intact (Rule 4).
    """
    reason: str = Field(..., min_length=1, max_length=2000)


class ClaimRejectRequest(BaseModel):
    """Body for POST /claims/{claim_id}/reject (Rule 6 user rejection)."""
    reason: str = Field(..., min_length=1, max_length=2000)


# ── GET /claims/{claim_id} ───────────────────────────────────────

class ClaimStatusResponse(BaseModel):
    """Current state of a persisted claim — returned by GET /claims/{claim_id}."""
    claim_id:               int
    user_id:                int
    policy_id:              int
    incident_date:          str
    incident_description:   str
    incident_location:      Optional[str] = None
    severity_label:         str
    reported_damage_amount: Optional[str] = None
    claim_status:           str
    ai_reasoning:           str
    created_at:             Optional[str] = None
    updated_at:             Optional[str] = None


# ── Health & Readiness ───────────────────────────────────────────

class HealthResponse(BaseModel):
    """Response from GET /health — used by K8s liveness probe."""
    status:      Literal["ok"] = "ok"
    service:     str           = "insureai-orchestrator"
    environment: str
    timestamp:   datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReadinessResponse(BaseModel):
    """Response from GET /ready — used by K8s readiness probe.

    `ready=False` means the app is up but dependencies aren't healthy
    (e.g. LLM provider key missing, DB unreachable). K8s won't route
    traffic until ready=True.
    """
    ready:    bool
    checks:   dict[str, bool]      # name -> ok flag
    details:  dict[str, str] = {}  # name -> error message (when not ok)


# ── Auth (POST /auth/token, dev-only) ────────────────────────────

class TokenRequest(BaseModel):
    """Body for POST /auth/token — dev-only helper to mint test JWTs.

    The real orchestrator validates tokens issued by an external IdP;
    this endpoint exists so we can develop end-to-end without one.
    """
    sub:  str = Field(..., min_length=1, max_length=120)
    role: Literal["analyst", "auditor", "admin"]


class TokenResponse(BaseModel):
    """Response from POST /auth/token."""
    access_token: str
    token_type:   Literal["bearer"] = "bearer"
    role:         Literal["analyst", "auditor", "admin"]
    expires_in:   int                      # seconds


# ── Error envelope ───────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Standard error envelope for non-2xx responses."""
    error:      str
    detail:     Optional[str] = None
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
