"""Mutation handler: defense-in-depth verification + ACL + audit fields + the write gate.

The gate is the reason this file carries a tool-aware pool stub rather than a single
canned response: `apply()` now re-derives the deterministic verification from source
immediately before the write, so the stub has to serve the policy, the claimant and the
document text the way the MCPs would.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from agents.mutation_handler import ApprovalContext, MutationHandler
from core import proposal as proposal_mod
from core.service_identity import VERIFICATION_SUB
from packages.common.enums import SeverityLabel
from packages.common.exceptions import ForbiddenError, MCPClientError
from packages.common.schemas.claim import ClaimDecision
from packages.common.schemas.triage import TriageReport

_KEY = "x" * 32
_AMOUNT = "3500.00"


class _RT:
    hmac_proposal_key = _KEY
    analyst_proposal_ttl_seconds = 300
    analyst_max_steps = 10
    analyst_timeout_seconds = 60


def _iso(offset_days: int) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


def _in_force_policy(owner_id: str) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "user_id": owner_id,
        "status": "ACTIVE",
        "effective_from": _iso(-90),
        "effective_to": _iso(+90),
    }


class _Pool:
    """Serves the three reads `_reverify` performs, plus the claims write.

    `policy` / `claimant` / `pdf_text` may be set to an Exception to simulate an
    unreachable MCP, which is how the fail-closed behaviour is exercised.
    """

    def __init__(
        self,
        *,
        owner_id: str = "",
        policy: Any = None,
        claimant: Any = None,
        pdf_text: Any = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.next_result: dict | Exception = {"claim": {"id": "fake-id", "status": "APPROVED"}}
        self.owner_id = owner_id or str(uuid4())
        self.policy = policy if policy is not None else _in_force_policy(self.owner_id)
        self.claimant = claimant
        self.pdf_text = pdf_text if pdf_text is not None else f"Monto estimado: ${_AMOUNT}"

    async def call(self, domain: str, tool: str, args: dict) -> dict:
        self.calls.append((domain, tool, args))
        if domain == "policies" and tool == "get_policy_by_id":
            return {"policy": self._unwrap(self.policy)}
        if domain == "users" and tool == "get_user_by_email":
            return {"user": self._unwrap(self.claimant)}
        if domain == "documents" and tool == "extract_text":
            return {"text": self._unwrap(self.pdf_text)}
        if isinstance(self.next_result, Exception):
            raise self.next_result
        return self.next_result

    @staticmethod
    def _unwrap(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def write_calls(self) -> list[tuple[str, str, dict]]:
        return [c for c in self.calls if c[1] in {"create_claim", "update_claim_status"}]

    def verification_calls(self) -> list[tuple[str, str, dict]]:
        return [c for c in self.calls if c[1] not in {"create_claim", "update_claim_status"}]


def _make_triage(
    pipeline_id: UUID, *, policy_id: UUID, amount: str = _AMOUNT, severity=SeverityLabel.MEDIUM
) -> TriageReport:
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


def _signed(pipeline_id: UUID, triage: TriageReport, decision: ClaimDecision):
    analyst = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="analyst",
        payload=triage.model_dump(mode="json"), key=_KEY,
    )
    auditor = proposal_mod.build_proposal(
        pipeline_id=pipeline_id, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    return analyst, auditor


async def _apply(pool: _Pool, approval: ApprovalContext, *, decision="APPROVED", amount=_AMOUNT):
    pipeline_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=uuid4(), amount=amount)
    claim_decision = _make_decision(pipeline_id, decision=decision)
    analyst_p, auditor_p = _signed(pipeline_id, triage, claim_decision)
    handler = MutationHandler(runtime=_RT(), pool=pool)
    return await handler.apply(
        approval=approval,
        analyst_proposal=analyst_p, analyst_triage=triage,
        auditor_proposal=auditor_p, auditor_decision=claim_decision,
    ), triage, analyst_p, auditor_p


_SELF = ApprovalContext(approver_sub="me@x.com", approver_role="ANALYST", submitter_sub="me@x.com")

pytestmark = pytest.mark.asyncio


# ── happy path ───────────────────────────────────────────────────────────────


async def test_happy_path_persists():
    pool = _Pool()
    result, _, analyst_p, auditor_p = await _apply(pool, _SELF)

    assert result.persisted is True
    writes = pool.write_calls()
    assert writes and writes[0][1] == "create_claim"

    audit = writes[0][2]["decision_metadata"]
    assert audit["analyst_signature"] == analyst_p.signature_hex
    assert audit["auditor_signature"] == auditor_p.signature_hex
    assert audit["approver_sub"] == "me@x.com"
    assert audit["decision"] == "APPROVED"
    # The verification as of the write is part of the immutable audit trail (Rule 6).
    assert audit["review_flags_at_write"] == []
    assert audit["verification_at_write"]["coverage"]["in_force"] is True


async def test_admin_can_approve_any():
    pool = _Pool()
    result, *_ = await _apply(
        pool,
        ApprovalContext(approver_sub="admin@x.com", approver_role="ADMIN", submitter_sub="other@x.com"),
    )
    assert result.persisted is True


async def test_verification_reads_use_the_service_principal():
    """The system's own facts must not be fetched with the approver's identity."""
    pool = _Pool()
    await _apply(pool, _SELF)
    reads = pool.verification_calls()
    assert reads, "expected the gate to re-read policy and document"
    for _domain, _tool, args in reads:
        assert args["caller_sub"] == VERIFICATION_SUB
        assert args["caller_role"] == "ADMIN"


# ── the write gate ───────────────────────────────────────────────────────────


async def test_gate_blocks_approved_when_policy_not_in_force():
    pool = _Pool()
    pool.policy = {"id": str(uuid4()), "user_id": pool.owner_id, "status": "ACTIVE",
                   "effective_from": _iso(-800), "effective_to": _iso(-30)}
    result, *_ = await _apply(pool, _SELF)

    assert result.persisted is False
    assert "no vigente" in (result.reason or "")
    assert pool.write_calls() == [], "nothing may be written once the gate refuses"


async def test_gate_blocks_approved_when_amount_is_unsupported():
    pool = _Pool(pdf_text="Monto estimado: $12.00")   # the triage reports $3500.00
    result, *_ = await _apply(pool, _SELF)

    assert result.persisted is False
    assert "Monto no respaldado" in (result.reason or "")


async def test_gate_blocks_cross_tenant_policy():
    """Coverage and identity both pass; only the ownership check catches this."""
    claimant_id = str(uuid4())
    pool = _Pool(claimant={"id": claimant_id, "email": "ana@x.com", "full_name": "Ana Martinez"})
    pool.pdf_text = f"Asegurado: Ana Martinez. Monto estimado: ${_AMOUNT}"
    # The policy belongs to somebody else entirely.
    pool.policy = _in_force_policy(str(uuid4()))

    result, *_ = await _apply(
        pool,
        ApprovalContext(approver_sub="me@x.com", approver_role="ANALYST",
                       submitter_sub="me@x.com", claimant_sub="ana@x.com"),
    )
    assert result.persisted is False
    assert "ajena" in (result.reason or "")


async def test_gate_fails_closed_when_verification_is_unavailable():
    """An unreachable MCP must block the approval, not wave it through."""
    pool = _Pool(policy=MCPClientError(public="upstream transport error"))
    result, *_ = await _apply(pool, _SELF)

    assert result.persisted is False
    assert "Verificación incompleta" in (result.reason or "")
    assert pool.write_calls() == []


async def test_gate_only_guards_approvals():
    """A REJECTED decision is not an approval — red flags must not block recording it."""
    pool = _Pool()
    pool.policy = {"id": str(uuid4()), "user_id": pool.owner_id, "status": "CANCELLED",
                   "effective_from": _iso(-90), "effective_to": _iso(+90)}
    result, *_ = await _apply(pool, _SELF, decision="REJECTED")

    assert result.persisted is True
    writes = pool.write_calls()
    assert writes[0][2]["status"] == "REJECTED"
    assert writes[0][2]["decision_metadata"]["review_flags_at_write"], (
        "the flags that stood at the write must still be recorded"
    )


# ── signature / coherence / ACL (unchanged guarantees) ───────────────────────


async def test_refuses_tampered_analyst_signature():
    pipeline_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=uuid4())
    decision = _make_decision(pipeline_id)
    analyst_p, auditor_p = _signed(pipeline_id, triage, decision)
    tampered = ("0" if analyst_p.signature_hex[0] != "0" else "1") + analyst_p.signature_hex[1:]
    analyst_bad = analyst_p.model_copy(update={"signature_hex": tampered})

    handler = MutationHandler(runtime=_RT(), pool=_Pool())
    with pytest.raises(ForbiddenError):
        await handler.apply(
            approval=_SELF,
            analyst_proposal=analyst_bad, analyst_triage=triage,
            auditor_proposal=auditor_p, auditor_decision=decision,
        )


async def test_refuses_pipeline_mismatch():
    p1, p2 = uuid4(), uuid4()
    triage = _make_triage(p1, policy_id=uuid4())
    decision = _make_decision(p1)
    analyst_p, _ = _signed(p1, triage, decision)
    auditor_other = proposal_mod.build_proposal(
        pipeline_id=p2, actor="auditor",
        payload=decision.model_dump(mode="json"), key=_KEY,
    )
    handler = MutationHandler(runtime=_RT(), pool=_Pool())
    with pytest.raises(ForbiddenError):
        await handler.apply(
            approval=_SELF,
            analyst_proposal=analyst_p, analyst_triage=triage,
            auditor_proposal=auditor_other, auditor_decision=decision,
        )


async def test_non_admin_approver_must_be_submitter():
    pipeline_id = uuid4()
    triage = _make_triage(pipeline_id, policy_id=uuid4())
    decision = _make_decision(pipeline_id)
    analyst_p, auditor_p = _signed(pipeline_id, triage, decision)

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
