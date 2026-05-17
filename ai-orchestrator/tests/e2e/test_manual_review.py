"""C4.3 — MANUAL_REVIEW & Auditor Approval.

When a claim ends up in MANUAL_REVIEW (either because the pipeline
escalated after 3 retries, or because an operator parked it there),
an authorised auditor must be able to drive it to a terminal state
through the orchestrator HTTP API.

This test seeds claims DIRECTLY in `claim_status = "MANUAL_REVIEW"`
(bypasses the LLM — cheap, deterministic) and exercises both human
exits through the real FastAPI → MCPRouter → claims MCP → Neon path:

    1. MANUAL_REVIEW + /approve → APPROVED   (audit trail preserves both)
    2. MANUAL_REVIEW + /reject  → REJECTED   (audit trail preserves both)
    3. Re-attempt on a now-terminal claim → 409 Conflict

The state machine that protects these transitions lives in the
update_claim_status MCP tool (C1.3) — this test is its E2E proof.

Skipped automatically unless RUN_E2E=1 (see conftest.py).

Cost: ~0 LLM tokens (real Neon writes only; no agent involvement).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


pytestmark = [pytest.mark.e2e]


# Realistic audit-grade reasons (clear C2.2's 20-char + control-char guards).
_APPROVE_REASON = (
    "Reviewed with regional manager; coverage extended retroactively. "
    "Manual approval is appropriate."
)
_REJECT_REASON = (
    "Reviewed evidence; coverage exclusion confirmed by underwriting. "
    "Manual rejection is appropriate."
)


def _seed_manual_review(db_engine, *, note: str) -> int:
    """Insert a claim row with claim_status = MANUAL_REVIEW. Returns its id."""
    with db_engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO claims (
                    user_id, policy_id, incident_date, incident_description,
                    incident_location, severity_label, claim_status, ai_reasoning
                )
                VALUES (
                    101, 1, '2025-06-15', :note,
                    'Test Location', 'LOW',
                    CAST('MANUAL_REVIEW' AS claim_status),
                    :reasoning
                )
                RETURNING claim_id
            """),
            {
                "note": note,
                "reasoning": (
                    "[RECOMMENDED: MANUAL_REVIEW] Pipeline escalated this claim "
                    "due to ambiguous coverage signals; human reviewer to decide."
                ),
            },
        ).fetchone()
        return int(row.claim_id)


def _delete_claim(db_engine, claim_id: int) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM claims WHERE claim_id = :id"),
            {"id": claim_id},
        )


# ── Approval branch ───────────────────────────────────────────────

def test_manual_review_to_approved(
    client, auditor_token: str, db_engine
) -> None:
    """MANUAL_REVIEW + auditor /approve → APPROVED with full audit trail."""
    claim_id = _seed_manual_review(db_engine, note="C4.3 approve seed")

    try:
        # GET → confirms starting state
        readback = client.get(
            f"/claims/{claim_id}",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert readback.status_code == 200, readback.text
        assert readback.json()["claim_status"] == "MANUAL_REVIEW"

        # POST /approve → state machine flips MANUAL_REVIEW → APPROVED
        approve = client.post(
            f"/claims/{claim_id}/approve",
            json={"reason": _APPROVE_REASON},
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert approve.status_code == 200, approve.text
        approved = approve.json()
        assert approved["claim_status"] == "APPROVED"
        assert approved["claim_id"]     == claim_id

        # Audit trail must contain BOTH:
        # 1. the original [RECOMMENDED: MANUAL_REVIEW] line
        # 2. the [MANUAL OVERRIDE … by octav.e2e] line
        reasoning = approved["ai_reasoning"]
        assert "[RECOMMENDED: MANUAL_REVIEW]" in reasoning, reasoning
        assert "MANUAL OVERRIDE"              in reasoning, reasoning
        assert "octav.e2e"                    in reasoning, reasoning
        assert _APPROVE_REASON[:40]           in reasoning, reasoning

        # Re-attempting → 409 (claim is now terminal)
        replay_via_reject = client.post(
            f"/claims/{claim_id}/reject",
            json={"reason": "should be blocked — claim is already APPROVED"},
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert replay_via_reject.status_code == 409, replay_via_reject.text

        print(f"\n[approve] claim_id={claim_id} MANUAL_REVIEW -> APPROVED OK")
    finally:
        _delete_claim(db_engine, claim_id)


# ── Rejection branch ──────────────────────────────────────────────

def test_manual_review_to_rejected(
    client, auditor_token: str, db_engine
) -> None:
    """MANUAL_REVIEW + auditor /reject → REJECTED with full audit trail."""
    claim_id = _seed_manual_review(db_engine, note="C4.3 reject seed")

    try:
        # POST /reject
        reject = client.post(
            f"/claims/{claim_id}/reject",
            json={"reason": _REJECT_REASON},
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert reject.status_code == 200, reject.text
        rejected = reject.json()
        assert rejected["claim_status"] == "REJECTED"

        # Same dual-line audit invariant as the approval branch
        reasoning = rejected["ai_reasoning"]
        assert "[RECOMMENDED: MANUAL_REVIEW]" in reasoning, reasoning
        assert "MANUAL OVERRIDE"              in reasoning, reasoning
        assert "octav.e2e"                    in reasoning, reasoning
        assert _REJECT_REASON[:40]            in reasoning, reasoning

        # Independent read-back
        readback = client.get(
            f"/claims/{claim_id}",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert readback.status_code == 200
        assert readback.json()["claim_status"] == "REJECTED"

        print(f"\n[reject] claim_id={claim_id} MANUAL_REVIEW -> REJECTED OK")
    finally:
        _delete_claim(db_engine, claim_id)


# ── Analyst cannot approve MANUAL_REVIEW (Rule 7 + Rule 4) ───────

def test_analyst_cannot_approve_manual_review(
    client, analyst_token: str, db_engine
) -> None:
    """Even on a MANUAL_REVIEW claim, an analyst is forbidden from /approve."""
    claim_id = _seed_manual_review(db_engine, note="C4.3 analyst block seed")
    try:
        forbidden = client.post(
            f"/claims/{claim_id}/approve",
            json={"reason": "Analyst attempting to approve — should be blocked."},
            headers={"Authorization": f"Bearer {analyst_token}"},
        )
        assert forbidden.status_code == 403, forbidden.text

        # State must be unchanged
        readback = client.get(
            f"/claims/{claim_id}",
            headers={"Authorization": f"Bearer {analyst_token}"},
        )
        assert readback.status_code == 200
        assert readback.json()["claim_status"] == "MANUAL_REVIEW"
        print(f"\n[rbac] analyst blocked from approving claim_id={claim_id} OK")
    finally:
        _delete_claim(db_engine, claim_id)
