"""C4.1 — JP01 Claim Full Happy Path → APPROVED.

Real-world end-to-end run with no mocks:

    1. analyst token POSTs the JP01 PDF to /claims/submit
       → runs the full LangGraph pipeline (analyst → coverage rules → auditor)
       → row persists in Neon with claim_status = "AWAITING_INPUT" (Rule 6)
       → response carries the auditor's RECOMMENDED decision

    2. auditor token POSTs /claims/{id}/approve with a real reason
       → row flips to claim_status = "APPROVED" with an audit trail line

    3. GET /claims/{id} confirms the final state in the DB.

Skipped automatically unless RUN_E2E=1 (see conftest.py).

Cost: ~$0.005 per run (one analyst + one auditor LLM cycle).
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text


pytestmark = [pytest.mark.e2e]


# Real reason text — must clear C2.2's 20-char threshold + no control chars.
_APPROVAL_REASON = (
    "E2E happy-path verification: claim reviewed manually; reinstating "
    "coverage decision after auditor recommendation."
)


def test_jp01_full_happy_path_ends_approved(
    client,
    analyst_token: str,
    auditor_token: str,
    jp01_pdf_path: str,
    db_engine,
) -> None:
    """Submit → AWAITING_INPUT → /approve → APPROVED, against real services."""

    # ── 1. Submit the JP01 claim ─────────────────────────────────
    t0 = time.perf_counter()
    submit = client.post(
        "/claims/submit",
        json={"pdf_path": jp01_pdf_path},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    submit_secs = time.perf_counter() - t0
    assert submit.status_code == 200, submit.text
    body = submit.json()

    assert body["pipeline_status"]   == "SUCCESS"
    assert body["claim_status"]      == "AWAITING_INPUT",  body
    assert body["recommended_decision"] in (
        "APPROVED", "REJECTED", "MANUAL_REVIEW",
    ), body
    assert body["persisted_claim_id"] is not None,         body
    assert body["summary"],                                body

    claim_id = body["persisted_claim_id"]
    print(
        f"\n[submit] claim_id={claim_id} "
        f"recommended={body['recommended_decision']} "
        f"latency={submit_secs:.1f}s"
    )

    try:
        # ── 2. Auditor approves the claim ────────────────────────
        approve = client.post(
            f"/claims/{claim_id}/approve",
            json={"reason": _APPROVAL_REASON},
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert approve.status_code == 200, approve.text
        approved = approve.json()
        assert approved["claim_status"] == "APPROVED"
        assert approved["claim_id"]     == claim_id
        # Audit trail must show OUR approver name (taken from JWT, not body)
        assert "octav.e2e" in approved["ai_reasoning"],         approved
        assert _APPROVAL_REASON[:50] in approved["ai_reasoning"], approved

        # ── 3. Read-back confirms the persisted state ────────────
        readback = client.get(
            f"/claims/{claim_id}",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert readback.status_code == 200, readback.text
        final = readback.json()
        assert final["claim_status"] == "APPROVED"
        assert final["claim_id"]     == claim_id
        print(f"[approved] claim_id={claim_id} final_status=APPROVED OK")

    finally:
        # ── Cleanup: remove the test row ─────────────────────────
        with db_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM claims WHERE claim_id = :id"),
                {"id": claim_id},
            )
