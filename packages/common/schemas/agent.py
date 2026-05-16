"""Shared agent output schemas (Rule 1).

These Pydantic models define the structured outputs produced by the
analyst and auditor agents. Anything outside `packages/common/` MUST
import these — never redefine.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from packages.common.enums import ClaimStatus, SeverityLabel


# ── Analyst output (B2.2) ──────────────────────────────────────────

class ClaimFlag(BaseModel):
    """A potential issue identified by the analyst.

    Flags ARE NOT decisions — the auditor reads these and decides outcome.
    """
    code:     str    = Field(..., description="Machine code, e.g. 'policy_expired'")
    message:  str    = Field(..., description="Human-readable explanation")
    severity: Literal["info", "warning", "critical"]


class TriageReport(BaseModel):
    """Structured output of the analyst_agent.

    The auditor_agent (B2.3) consumes this report and produces a
    ClaimDecision (APPROVED / REJECTED / MANUAL_REVIEW).
    """
    status:   Literal["EXTRACTED", "EXTRACTION_FAILED", "USER_NOT_FOUND"]
    claim:    dict[str, Any] | None       = None
    user:     dict[str, Any] | None       = None
    policies: list[dict[str, Any]]        = Field(default_factory=list)
    flags:    list[ClaimFlag]             = Field(default_factory=list)
    summary:  str                         = ""


# ── Auditor output (B2.3) ──────────────────────────────────────────

class ClaimDecision(BaseModel):
    """Final decision produced by the auditor_agent.

    This is what gets shown to the user (Rule 6) and persisted via the
    claims MCP once user approval is granted (Phase C).
    """
    decision:               ClaimStatus       # APPROVED | REJECTED | MANUAL_REVIEW
    claim_id:               Optional[str] = None   # external id from the PDF, e.g. "CASE-2026-0089"
    user_id:                int
    policy_id:              Optional[int] = None   # null if no eligible policy found
    severity_label:         SeverityLabel
    reported_damage_amount: Optional[Decimal] = None
    incident_date:          str               # ISO-8601
    incident_description:   str
    incident_location:      Optional[str] = None
    ai_reasoning:           str               # MUST be present (Rule 4)
    next_action:            Literal[
        "process_payout",
        "send_rejection_letter",
        "escalate_to_human",
    ]
    # Set by the auditor code from the REAL create_claim tool result, never
    # by the LLM. None means create_claim was not actually called.
    persisted_claim_id:     Optional[int] = None


# ── Orchestration output (B3.2) ────────────────────────────────────

class ClaimProcessingResult(BaseModel):
    """Full pipeline output — what AgentRouter.process_claim() returns.

    Consumed by FastAPI (C1.4) and any other caller that needs the
    end-to-end result with timing + retry telemetry.
    """
    pdf_path:           str
    pipeline_status:    Literal[
        "SUCCESS",
        "ANALYST_FAILED",
        "AUDITOR_FAILED",
    ]
    triage_report:      Optional[dict[str, Any]] = None
    claim_decision:     Optional[dict[str, Any]] = None
    analyst_retries:    int = 0
    auditor_retries:    int = 0
    analyst_latency_ms: int = 0
    auditor_latency_ms: int = 0
    total_latency_ms:   int = 0
    error_message:      Optional[str] = None
