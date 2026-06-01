"""Mutation handler: defense-in-depth verification + ACL + audit fields."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from agents.mutation_handler import ApprovalContext, MutationHandler
from core import proposal as proposal_mod
from packages.common.enums import SeverityLabel
from packages.common.exceptions import ForbiddenError
from packages.common.schemas.claim import ClaimDecision
from packages.common.schemas.triage import TriageReport


_KEY = "x" * 32


class _RT:
    hmac_proposal_key = _KEY
    analyst_proposal_ttl_seconds = 300
    analyst_max_steps = 10
    analyst_timeout_seconds = 60


class _Pool:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.next_result: dict | Exception = {"claim": {"id": "fake-id", "status": "APPROVED"}}

    async def call(self, domain, tool, args):
        if isinstance(self.next_result, Exception):
            raise self.next_result
        self.calls.append((domain, tool, args))
        return self.next_result


def _make_triage(pipeline_id: UUID, *, policy_id: UUID, amount: str = "3500.00", severity=SeverityLabel.MEDIUM) -> TriageReport:
    return TriageReport(
        pipeline_id=pipeline_id, policy_id=policy_id, pdf_sha256="a" * 64,
        reported_damage_amount=Decimal(amount), severity=severity, summary="ok",
        evidence_quotes=("evidence",),
    )


def _make_decision(pipeline_id: UUID, *, decision="APPROVED") -> ClaimDecision:
    return ClaimDecision(
        claim_id=pipeline_id, decision=decision, reason="r",
        evidence=("e",), model="m", model_version="v",
        decided_at=datetime.now(tz=timezone.utc),
    )


@pytest.mark.asyncio
async def test_happy_path_persists():
    pipeline_id = uuid4(); policy_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=policy_id)
    decision = _make_decision(pipeline_id)
    analyst_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )

    pool = _Pool()
    handler = MutationHandler(runtime=_RT(), pool=pool)
    result = await handler.apply(
        approval=ApprovalContext(approver_sub="me@x.com", approver_role="ANALYST", submitter_sub="me@x.com"),
        analyst_proposal=analyst_p, analyst_triage=triage,
        auditor_proposal=auditor_p, auditor_decision=decision,
    )
    assert result.persisted is True
    assert pool.calls and pool.calls[0][1] == "create_claim"
    sent_args = pool.calls[0][2]
    # Audit packet must include both signatures, the approver, and the decision.
    audit = sent_args["decision_metadata"]
    assert audit["analyst_signature"] == analyst_p.signature_hex
    assert audit["auditor_signature"] == auditor_p.signature_hex
    assert audit["approver_sub"] == "me@x.com"
    assert audit["decision"] == "APPROVED"


@pytest.mark.asyncio
async def test_refuses_tampered_analyst_signature():
    pipeline_id = uuid4(); policy_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=policy_id)
    decision = _make_decision(pipeline_id)
    analyst_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    # Tamper: flip one char of the signature.
    tampered_sig = ("0" if analyst_p.signature_hex[0] != "0" else "1") + analyst_p.signature_hex[1:]
    analyst_p_bad = analyst_p.model_copy(update={"signature_hex": tampered_sig})

    handler = MutationHandler(runtime=_RT(), pool=_Pool())
    with pytest.raises(ForbiddenError):
        await handler.apply(
            approval=ApprovalContext(approver_sub="me@x.com", approver_role="ANALYST", submitter_sub="me@x.com"),
            analyst_proposal=analyst_p_bad, analyst_triage=triage,
            auditor_proposal=auditor_p, auditor_decision=decision,
        )


@pytest.mark.asyncio
async def test_refuses_pipeline_mismatch():
    p1 = uuid4(); p2 = uuid4(); policy_id = uuid4()
    triage = _make_triage(p1, policy_id=policy_id)
    decision = _make_decision(p1)
    analyst_p = proposal_mod.build_proposal(
        pipeline_id=p1, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor_p_other = proposal_mod.build_proposal(
        pipeline_id=p2, actor="auditor",   # different pipeline!
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    handler = MutationHandler(runtime=_RT(), pool=_Pool())
    with pytest.raises(ForbiddenError):
        await handler.apply(
            approval=ApprovalContext(approver_sub="me@x.com", approver_role="ANALYST", submitter_sub="me@x.com"),
            analyst_proposal=analyst_p, analyst_triage=triage,
            auditor_proposal=auditor_p_other, auditor_decision=decision,
        )


@pytest.mark.asyncio
async def test_non_admin_approver_must_be_submitter():
    pipeline_id = uuid4(); policy_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=policy_id)
    decision = _make_decision(pipeline_id)
    analyst_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    handler = MutationHandler(runtime=_RT(), pool=_Pool())
    with pytest.raises(ForbiddenError):
        await handler.apply(
            approval=ApprovalContext(
                approver_sub="someone@else.com", approver_role="ANALYST",
                submitter_sub="real-owner@x.com",
            ),
            analyst_proposal=analyst_p, analyst_triage=triage,
            auditor_proposal=auditor_p, auditor_decision=decision,
        )


@pytest.mark.asyncio
async def test_admin_can_approve_any():
    pipeline_id = uuid4(); policy_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=policy_id)
    decision = _make_decision(pipeline_id)
    analyst_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor_p = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    pool = _Pool()
    handler = MutationHandler(runtime=_RT(), pool=pool)
    result = await handler.apply(
        approval=ApprovalContext(
            approver_sub="admin@x.com", approver_role="ADMIN",
            submitter_sub="other@x.com",
        ),
        analyst_proposal=analyst_p, analyst_triage=triage,
        auditor_proposal=auditor_p, auditor_decision=decision,
    )
    assert result.persisted is True
