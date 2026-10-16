"""Drive one case through the real `/analyze` → `/proposal` → `/confirm` handlers.

The human in the loop is simulated as the most permissive realistic reviewer: one who
reads the screen and clicks approve. That is the right adversarial assumption — a control
that only holds because the reviewer was attentive is not a control, and the whole point
of the suite is to find out what the system still guarantees when the reviewer is not.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from auth import Role, UserPrincipal
from routes import agent_router

from evals.cases import Case
from evals.catalog import World
from evals.harness.app import InMemoryHITL, RuntimeView, ScopedPool, build_request
from evals.harness.fake_mcp import RecordingMCPPool
from evals.harness.llm import CompromisedAnalystLLM, CompromisedAuditorLLM


@dataclass
class CaseRun:
    """Everything observable about one case execution, for the invariants to inspect."""

    case: Case
    pipeline_id: UUID
    pool: RecordingMCPPool
    hitl: InMemoryHITL
    analyst_llm: CompromisedAnalystLLM
    auditor_llm: CompromisedAuditorLLM
    analyze: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None
    confirm: dict[str, Any] | None = None
    stored_claim: dict[str, Any] | None = None
    tools_advertised: list[str] = field(default_factory=list)
    confirm_skipped: str | None = None
    duration_s: float = 0.0
    error: str | None = None

    # ── convenience accessors used by the invariants ────────────────────────

    @property
    def triage(self) -> dict[str, Any]:
        return (self.analyze or {}).get("triage") or {}

    @property
    def proposal(self) -> dict[str, Any]:
        return (self.analyze or {}).get("proposal") or {}

    @property
    def verification(self) -> dict[str, Any]:
        detail = self.detail or {}
        return {
            "coverage": detail.get("coverage"),
            "ownership": detail.get("ownership"),
            "identity": detail.get("identity"),
            "amount": detail.get("amount"),
        }

    @property
    def outcome(self) -> str:
        return str((self.analyze or {}).get("status") or "error")


async def run_case(case: Case, world: World, *, pipeline_id: UUID) -> CaseRun:
    base = RecordingMCPPool(world=world)
    runtime = RuntimeView(claim_upload_dir=str(world.upload_dir))

    analyst_llm = CompromisedAnalystLLM(case)
    auditor_llm = CompromisedAuditorLLM(case)

    # Separate pool views per stage — see ScopedPool for why this matters.
    from agents.analyst import Analyst
    from agents.auditor import Auditor
    from agents.mutation_handler import MutationHandler

    analyst = Analyst(runtime=runtime, pool=ScopedPool(base, "agent"), llm=analyst_llm)
    auditor = Auditor(runtime=runtime, pool=ScopedPool(base, "agent"), llm=auditor_llm)
    mutation = MutationHandler(runtime=runtime, pool=ScopedPool(base, "mutation"))
    hitl = InMemoryHITL()

    request = build_request({
        "settings": runtime,
        "pool": ScopedPool(base, "verification"),
        "analyst": analyst,
        "auditor": auditor,
        "mutation": mutation,
        "hitl": hitl,
        "redis": None,
    })
    user = UserPrincipal(sub=case.operator_sub, role=Role(case.operator_role.lower()))

    run = CaseRun(
        case=case,
        pipeline_id=pipeline_id,
        pool=base,
        hitl=hitl,
        analyst_llm=analyst_llm,
        auditor_llm=auditor_llm,
    )

    started = time.perf_counter()
    try:
        analyze_response = await agent_router.analyze(
            agent_router.AnalyzeRequest(pipeline_id=pipeline_id, claimant_sub=case.claimant_sub),
            request,
            user,
        )
        run.analyze = analyze_response.model_dump(mode="json")

        proposal_id = run.analyze.get("proposal_id")
        if proposal_id:
            detail = await agent_router.proposal_detail(UUID(proposal_id), request, user)
            run.detail = detail.model_dump(mode="json")
            await _confirm(run, request, user)
    except Exception as exc:  # noqa: BLE001 — a crash is itself a finding, not a test error
        run.error = f"{type(exc).__name__}: {exc}"
    finally:
        run.duration_s = time.perf_counter() - started

    if analyst_llm.captured:
        run.tools_advertised = list(analyst_llm.captured[0]["tools"])
    run.stored_claim = _stored_claim(run)
    return run


async def _confirm(run: CaseRun, request: Any, user: UserPrincipal) -> None:
    """Simulate the reviewer approving whatever the auditor proposed."""
    from auth.roles import has_role_or_higher

    decision_id = (run.detail or {}).get("audit_decision_id")
    if not decision_id:
        run.confirm_skipped = "the auditor produced no signed decision"
        return
    if not has_role_or_higher(user.role, Role.AUDITOR):
        # POST /confirm requires AUDITOR or higher; an ANALYST operator cannot reach it.
        run.confirm_skipped = f"role {user.role.value!r} cannot call /confirm"
        return

    response = await agent_router.confirm(
        agent_router.ConfirmRequest(decision_id=UUID(decision_id), approval="approve"),
        request,
        user,
    )
    run.confirm = response.model_dump(mode="json")


def _stored_claim(run: CaseRun) -> dict[str, Any] | None:
    """The durable claim row this case produced, if any."""
    if run.confirm and (run.confirm.get("claim") or {}).get("id"):
        return run.confirm["claim"]
    claim_id = (run.hitl.proposals.get(str((run.analyze or {}).get("proposal_id")), {}) or {}).get("claim_id")
    if claim_id and claim_id in run.pool.rows:
        return run.pool.rows[claim_id]
    return next(iter(run.pool.rows.values()), None)
