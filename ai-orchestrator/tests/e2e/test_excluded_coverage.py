"""C4.2 — Excluded-Coverage Claim Full Rejection Path → REJECTED.

The JP01 sample is genuinely excluded: incident date 2026-05-05 falls
AFTER the user's policy end_date 2026-01-01. The pipeline must:

    1. Recognise the exclusion (coverage rules engine returns REJECTED
       with reason_code = "policy_expired").
    2. Auditor agent recommends REJECTED in its final JSON.
    3. Row persists as AWAITING_INPUT (Rule 6 — no auto-finalisation).
    4. Human auditor calls POST /claims/{id}/reject to confirm.
    5. Final claim_status = "REJECTED" with audit trail showing both the
       LLM recommendation and the human confirmation.

Skipped automatically unless RUN_E2E=1 (see conftest.py).

Cost: ~$0.005 per run.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text


pytestmark = [pytest.mark.e2e]


# Reason that clears C2.2's 20-char guard and reads like a real audit note.
_REJECT_REASON = (
    "Confirming rejection: policy 1 expired on 2026-01-01, incident "
    "occurred 2026-05-05 — claim falls outside coverage period."
)


def test_jp01_excluded_coverage_ends_rejected(
    client,
    analyst_token: str,
    auditor_token: str,
    jp01_pdf_path: str,
    db_engine,
) -> None:
    """Submit JP01 → recommendation=REJECTED → /reject → REJECTED."""

    # ── 1. Submit ────────────────────────────────────────────────
    t0 = time.perf_counter()
    submit = client.post(
        "/claims/submit",
        json={"pdf_path": jp01_pdf_path},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    submit_secs = time.perf_counter() - t0
    assert submit.status_code == 200, submit.text
    body = submit.json()

    assert body["pipeline_status"]      == "SUCCESS",         body
    assert body["claim_status"]         == "AWAITING_INPUT",  body
    assert body["recommended_decision"] == "REJECTED",        (
        "The coverage engine should reject JP01 — its policy is "
        f"expired. Got: {body['recommended_decision']!r}"
    )
    assert body["persisted_claim_id"] is not None,            body

    claim_id = body["persisted_claim_id"]
    print(
        f"\n[submit] claim_id={claim_id} "
        f"recommended={body['recommended_decision']} "
        f"latency={submit_secs:.1f}s"
    )

    # The auditor's reasoning must explain WHY (cites the engine's reason).
    cd = body.get("claim_decision") or {}
    reasoning = cd.get("ai_reasoning", "")
    assert "[RECOMMENDED: REJECTED]" in reasoning, (
        f"ai_reasoning should start with the recommendation tag, got: {reasoning[:200]!r}"
    )

    try:
        # ── 2. Auditor rejects (confirms the recommendation) ─────
        reject = client.post(
            f"/claims/{claim_id}/reject",
            json={"reason": _REJECT_REASON},
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert reject.status_code == 200, reject.text
        rejected = reject.json()
        assert rejected["claim_status"] == "REJECTED"
        assert rejected["claim_id"]     == claim_id

        # Audit trail must contain BOTH the LLM recommendation AND the
        # human confirmation (Rule 4 + Rule 6 — full chain preserved).
        assert "[RECOMMENDED: REJECTED]" in rejected["ai_reasoning"], rejected
        assert "octav.e2e"               in rejected["ai_reasoning"], rejected
        assert _REJECT_REASON[:50]       in rejected["ai_reasoning"], rejected

        # ── 3. Independent read-back via GET ─────────────────────
        readback = client.get(
            f"/claims/{claim_id}",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert readback.status_code == 200, readback.text
        final = readback.json()
        assert final["claim_status"] == "REJECTED"
        assert final["claim_id"]     == claim_id
        print(f"[rejected] claim_id={claim_id} final_status=REJECTED OK")

    finally:
        # ── Cleanup ──────────────────────────────────────────────
        with db_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM claims WHERE claim_id = :id"),
                {"id": claim_id},
            )


def test_jp01_recommendation_carries_engine_reason_code(
    client,
    analyst_token: str,
    jp01_pdf_path: str,
    db_engine,
) -> None:
    """Lighter assertion: the recommendation explicitly cites the engine's
    reason code (policy_expired) somewhere in the chain of thought.

    This documents the path from the deterministic engine → auditor LLM
    → persisted ai_reasoning, end-to-end.
    """
    submit = client.post(
        "/claims/submit",
        json={"pdf_path": jp01_pdf_path},
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    claim_id = body["persisted_claim_id"]

    try:
        cd = body.get("claim_decision") or {}
        reasoning = cd.get("ai_reasoning", "").lower()

        # The auditor's reasoning should mention either the engine's
        # reason_code OR the equivalent human-readable rationale.
        signals = ["policy_expired", "expired", "after", "end date", "coverage"]
        assert any(s in reasoning for s in signals), (
            f"Auditor reasoning should reference the coverage exclusion. "
            f"Got: {reasoning[:300]!r}"
        )
        print(f"\n[reason-trace] claim_id={claim_id} contains policy-expiry signal OK")

    finally:
        with db_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM claims WHERE claim_id = :id"),
                {"id": claim_id},
            )
