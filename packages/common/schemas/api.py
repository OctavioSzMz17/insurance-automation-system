"""HTTP request/response schemas for the orchestrator API (Phase C).

Rule 1 — All Pydantic models live in packages/common/.
Rule 11 — Strong typing everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── POST /claims/submit ──────────────────────────────────────────

class ClaimSubmitRequest(BaseModel):
    """Body for POST /claims/submit.

    Phase C C1.1 accepts only a pdf_path (orchestrator + MCP share a
    filesystem volume). Multipart upload (`UploadFile`) will be added
    when we deploy with the shared PVC (Phase B1.4 already provisions it).

    Fix B — pdf_path is validated at parse time:
        - Must be non-empty after stripping whitespace
        - Must not contain null bytes (path injection)
        - Must not contain '..' components (directory traversal)
        - Must be absolute (relative paths are ambiguous across services)
    The route layer adds a second check: the resolved path must fall
    within CLAIM_UPLOAD_DIR (defence-in-depth).
    """
    pdf_path: str = Field(
        ...,
        description="Absolute filesystem path to the claim PDF.",
        min_length=1,
    )

    @field_validator("pdf_path")
    @classmethod
    def validate_pdf_path(cls, v: str) -> str:
        """Reject unsafe path patterns before the value ever reaches a route.

        Checks are OS-agnostic so the validator works correctly on both
        Windows (dev) and Linux/Docker (production):
            - null bytes: never valid in any path
            - '..' components: traversal regardless of separator used
            - relative paths: os.path.isabs handles both / and C:\\ forms
        """
        import os

        v = v.strip()
        if not v:
            raise ValueError("pdf_path must not be blank")

        # Null bytes terminate C strings — never valid in a path.
        if "\x00" in v:
            raise ValueError("pdf_path must not contain null bytes")

        # Directory traversal via '..' — split on both separators so
        # mixed paths like /tmp/../etc are caught on any OS.
        parts = v.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError("pdf_path must not contain '..' components")

        # os.path.isabs is OS-aware: True for /tmp/... on POSIX and
        # C:\... on Windows; False for relative paths on both.
        if not os.path.isabs(v):
            raise ValueError("pdf_path must be an absolute path")

        return v


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


class ClaimConfirmRequest(BaseModel):
    """Body for POST /claims/{claim_id}/confirm — unified Rule 6 HITL gate.

    The human auditor supplies a final decision and a written reason.
    Actor identity comes from the JWT `sub` claim — never from the body.
    """
    decision: Literal["APPROVED", "REJECTED", "MANUAL_REVIEW"]
    reason: str = Field(..., min_length=1, max_length=2000)


class ClaimConfirmResponse(BaseModel):
    """Response from POST /claims/{claim_id}/confirm."""
    claim_id:   int
    new_status: str
    actor:      str
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── SSE Stream Ticket (Fix 3 — Rule 7 token hardening) ───────────

class StreamTicketResponse(BaseModel):
    """Response from POST /claims/{request_id}/stream-ticket.

    The client opens the SSE connection using ?ticket=<ticket> instead
    of ?token=JWT, preventing JWT leakage into logs and browser history.
    Tickets are single-use and expire after `expires_in` seconds.
    """
    ticket:     str          # single-use UUID
    expires_in: int = 30     # seconds until expiry
    scoped_to:  str          # request_id this ticket grants access to


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


# ── GET /claims (list) ───────────────────────────────────────────

class ClaimListResponse(BaseModel):
    """Response from GET /claims — paginated list of claim rows."""
    claims: list[ClaimStatusResponse]
    total:  int
    limit:  int
    offset: int


# ── GET /claims/{id}/trace (D5.1) ────────────────────────────────

class TraceObservationItem(BaseModel):
    """One node from the Langfuse trace tree, flattened.

    Tree relationships are conveyed via `parent_id`; the UI rebuilds
    the hierarchy client-side. Optional fields are `None` when Langfuse
    didn't report them (e.g. tool-call spans don't have token counts).
    """
    id:            str
    parent_id:     Optional[str] = None
    name:          Optional[str] = None
    type:          Optional[str] = None      # SPAN | GENERATION | EVENT
    started_at:    Optional[str] = None
    ended_at:      Optional[str] = None
    latency_ms:    Optional[int] = None
    cost_usd:      Optional[str] = None      # serialised Decimal (Rule 2)
    input_tokens:  Optional[int] = None
    output_tokens: Optional[int] = None
    level:         Optional[str] = None      # DEFAULT | DEBUG | WARNING | ERROR
    status_message: Optional[str] = None


class ClaimTraceResponse(BaseModel):
    """Response from GET /claims/{claim_id}/trace.

    Returns the most-recent trace associated with the claim plus the
    deep-link URL the UI can offer for "Open in Langfuse".
    """
    claim_id:        int
    trace_id:        str
    name:            Optional[str] = None
    total_latency_ms: Optional[int] = None
    total_cost_usd:  Optional[str] = None     # Decimal as string
    started_at:      Optional[str] = None
    ended_at:        Optional[str] = None
    langfuse_url:    Optional[str] = None     # browser-accessible deep-link
    observations:    list[TraceObservationItem] = []


# ── GET /users/{id} ──────────────────────────────────────────────

class UserResponse(BaseModel):
    """Public user profile — wraps legacy-provider's get_user_by_id."""
    user_id: int
    name:    str
    email:   str


# ── GET /users/{id}/policies ─────────────────────────────────────

class PolicyResponse(BaseModel):
    """Single policy row — wraps legacy-provider's policy dict.

    Monetary fields are strings (Rule 2 — never round Decimals via float)
    and dates are ISO-8601 strings.

    `status` is the **effective** status (e.g. an `ACTIVE` row whose
    `end_date` is in the past is exposed as `EXPIRED`). `stored_status`
    preserves the raw DB value so the UI can flag the drift and the audit
    trail keeps both signals. See packages/common/business_rules.py.
    """
    policy_id:          int
    user_id:            int
    type:               str
    status:             str
    stored_status:      Optional[str] = None
    deductible_usd:     str
    coverage_limit_usd: str
    start_date:         str
    end_date:           str


class PolicyListResponse(BaseModel):
    """Response from GET /users/{id}/policies."""
    user_id:  int
    policies: list[PolicyResponse]
    total:    int


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
