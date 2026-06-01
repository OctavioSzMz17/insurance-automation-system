"""Auditor agent — LangGraph ReAct, mirror of the analyst with a different prompt and output.

Receives a previously emitted (and HMAC-verified) TriageReport from the analyst, plus the
caller's identity. RE-FETCHES via the read tools, then emits a `ClaimDecision` with
APPROVED | REJECTED | MANUAL_REVIEW.

The auditor is the second-opinion gate before the human-in-the-loop confirms. It is
ALLOWED to disagree with the analyst — that disagreement is its whole job.

Same security invariants as the analyst:
- caller_sub / caller_role server-controlled on every tool call (Rule 7).
- pipeline_id server-controlled on documents tools (Rule 7, 13).
- No write tools advertised. Even if the LLM hallucinates one, dispatch rejects it (Rule 4).
- max_steps + timeout → MANUAL_REVIEW.
- Validation feedback loop bounded by MAX_VALIDATION_RETRIES.
- ClaimDecision uses extra="forbid", strict=True — the security boundary for the auditor (Rule 4).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from packages.common.exceptions import BusinessError, MCPClientError
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.triage import TriageReport

from core import proposal as proposal_mod
from core.config_runtime import OrchestratorRuntimeView
from core.logging_bridge import get_logger
from core.mcp_client import MCPClientPool
from core.models import LLMClient
from core.prompts.auditor import SYSTEM_PROMPT
from core.tools import build_tool_specs, split_tool_name

_logger = get_logger(__name__)
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_MAX_VALIDATION_RETRIES = 3


@dataclass(frozen=True)
class AuditRequest:
    pipeline_id: UUID
    caller_sub: str
    caller_role: str
    triage: TriageReport          # already HMAC-verified by the caller
    analyst_proposal: ClaimProposal  # carry through so it can be stored alongside the decision
    # Deterministic, system-computed checks (coverage / identity / amount). TRUSTED
    # ground truth — fed to the auditor so it never has to (unreliably) re-derive them.
    verification: dict[str, Any] | None = None


def _format_verification(v: dict[str, Any] | None) -> str:
    """Render the deterministic checks as a trusted facts block for the auditor."""
    if not v:
        return ""
    lines: list[str] = []
    cov = v.get("coverage")
    if cov:
        lines.append(f"- Policy coverage: {'IN FORCE' if cov.get('in_force') else 'NOT IN FORCE'} — {cov.get('reason')}")
    idn = v.get("identity")
    if idn and idn.get("match") is not None:
        lines.append(f"- Claimant vs document identity: {'MATCH' if idn.get('match') else 'MISMATCH'} — {idn.get('reason')}")
    amt = v.get("amount")
    if amt and amt.get("supported") is not None:
        lines.append(f"- Reported amount vs document: {'SUPPORTED' if amt.get('supported') else 'NOT SUPPORTED'} — {amt.get('reason')}")
    if not lines:
        return ""
    return (
        "SYSTEM-VERIFIED FACTS (deterministic — computed by the system, NOT by any LLM "
        "or document text. Treat as GROUND TRUTH that overrides the analyst):\n"
        + "\n".join(lines) + "\n\n"
    )


@dataclass
class AuditorOutcome:
    status: Literal["success", "manual_review"]
    decision: ClaimDecision | None = None
    signed_proposal: ClaimProposal | None = None  # the auditor's OWN signed proposal (carries the decision)
    reason: str | None = None
    steps_used: int = 0


class _State(TypedDict, total=False):
    pipeline_id: str
    caller_sub: str
    caller_role: str
    analyst_triage: dict[str, Any]   # serialized so it can ride in the user message safely

    messages: list[dict[str, Any]]
    step: int
    deadline: float
    validation_retries: int

    outcome_status: Literal["success", "manual_review"]
    decision: ClaimDecision | None
    signed_proposal: ClaimProposal | None
    reason: str | None


class Auditor:
    def __init__(self, *, runtime: OrchestratorRuntimeView, pool: MCPClientPool, llm: LLMClient) -> None:
        self._runtime = runtime
        self._pool = pool
        self._llm = llm
        # Auditor sees the same tools as the analyst — read-only spec.
        self._tool_specs = build_tool_specs()
        self._graph = self._build_graph()

    # ── public ──────────────────────────────────────────────────────────────

    async def run(self, req: AuditRequest) -> AuditorOutcome:
        analyst_triage_dump = req.triage.model_dump(mode="json")
        initial: _State = {
            "pipeline_id": str(req.pipeline_id),
            "caller_sub": req.caller_sub,
            "caller_role": req.caller_role,
            "analyst_triage": analyst_triage_dump,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": (
                        f"pipeline_id: {req.pipeline_id}\n"
                        f"caller_sub: {req.caller_sub}\n"
                        f"caller_role: {req.caller_role}\n\n"
                        f"{_format_verification(req.verification)}"
                        "Below is the analyst's TriageReport (HMAC-verified by the system). "
                        "Re-fetch the policy and the PDF to verify independently. The "
                        "SYSTEM-VERIFIED FACTS above are authoritative — if any of them is a "
                        "red flag (coverage NOT IN FORCE, identity MISMATCH, amount NOT "
                        "SUPPORTED), you must NOT return APPROVED. Then emit your ClaimDecision "
                        "as a fenced ```json block.\n\n"
                        "<UNTRUSTED_INPUT>\n"
                        f"{json.dumps(analyst_triage_dump, default=str, indent=2)}\n"
                        "</UNTRUSTED_INPUT>"
                    ),
                }],
            }],
            "step": 0,
            "deadline": time.monotonic() + self._runtime.analyst_timeout_seconds,
            "validation_retries": 0,
        }
        final: _State = await self._graph.ainvoke(
            initial, config={"recursion_limit": self._runtime.analyst_max_steps * 3 + 10}
        )
        status = final.get("outcome_status", "manual_review")
        if status == "success":
            return AuditorOutcome(
                status="success",
                decision=final.get("decision"),
                signed_proposal=final.get("signed_proposal"),
                steps_used=final.get("step", 0),
            )
        return AuditorOutcome(
            status="manual_review",
            reason=final.get("reason", "unspecified"),
            steps_used=final.get("step", 0),
        )

    # ── graph ───────────────────────────────────────────────────────────────

    def _build_graph(self):
        g: StateGraph = StateGraph(_State)
        g.add_node("think_act", self._think_act)
        g.add_node("run_tools", self._run_tools)
        g.add_node("validate_sign", self._validate_sign)
        g.add_node("escalate", self._escalate)
        g.add_edge(START, "think_act")
        g.add_conditional_edges(
            "think_act", self._route_after_think,
            {"run_tools": "run_tools", "validate_sign": "validate_sign", "escalate": "escalate"},
        )
        g.add_edge("run_tools", "think_act")
        g.add_conditional_edges(
            "validate_sign", self._route_after_validate,
            {"think_act": "think_act", "end": END, "escalate": "escalate"},
        )
        g.add_edge("escalate", END)
        return g.compile()

    # ── nodes ───────────────────────────────────────────────────────────────

    async def _think_act(self, state: _State) -> dict[str, Any]:
        if time.monotonic() > state["deadline"]:
            return {"outcome_status": "manual_review", "reason": f"timeout after {state['step']} steps"}
        if state["step"] >= self._runtime.analyst_max_steps:
            return {"outcome_status": "manual_review",
                    "reason": f"reached max_steps={self._runtime.analyst_max_steps}"}

        turn = await self._llm.complete(
            system=SYSTEM_PROMPT, messages=state["messages"], tools=self._tool_specs
        )
        _logger.info(
            "auditor step",
            extra={"event": "auditor.step", "pipeline_id": state["pipeline_id"],
                   "step": state["step"] + 1, "stop_reason": turn.stop_reason},
        )
        return {
            "messages": state["messages"] + [{"role": "assistant", "content": turn.content_blocks}],
            "step": state["step"] + 1,
        }

    async def _run_tools(self, state: _State) -> dict[str, Any]:
        last = state["messages"][-1]
        tool_uses = [b for b in last["content"] if b["type"] == "tool_use"]
        results: list[dict[str, Any]] = []
        for tu in tool_uses:
            results.append(await self._invoke_tool(state, tu))
        return {"messages": state["messages"] + [{"role": "user", "content": results}]}

    async def _validate_sign(self, state: _State) -> dict[str, Any]:
        last = state["messages"][-1]
        payload = self._extract_json(last["content"])
        if payload is None:
            return {"reason": "auditor could not produce a structured decision"}

        decision_str = str(payload.get("decision", "")).upper()
        if decision_str not in {"APPROVED", "REJECTED", "MANUAL_REVIEW"}:
            return self._maybe_retry(state, "decision must be APPROVED | REJECTED | MANUAL_REVIEW")

        evidence = payload.get("evidence", [])
        if not isinstance(evidence, list):
            return self._maybe_retry(state, "evidence must be a JSON array of strings")
        if decision_str in {"APPROVED", "REJECTED"} and not evidence:
            return self._maybe_retry(state, "evidence is required for APPROVED or REJECTED")

        reason = str(payload.get("reason", "")).strip()
        if not reason:
            return self._maybe_retry(state, "reason is required")
        if len(reason) > 2000:
            reason = reason[:2000]

        try:
            decision = ClaimDecision(
                claim_id=UUID(state["pipeline_id"]),  # claim_id == pipeline_id for the active run
                decision=decision_str,  # type: ignore[arg-type]
                reason=reason,
                evidence=tuple(str(e) for e in evidence),
                model=getattr(self._llm, "model", "unknown"),
                model_version=getattr(self._llm, "model", "unknown"),
                decided_at=datetime.now(tz=timezone.utc),
            )
        except ValidationError as exc:
            return self._maybe_retry(state, f"ClaimDecision schema invalid: {exc.errors()[0]['msg']}")

        signed = proposal_mod.build_proposal(
            pipeline_id=UUID(state["pipeline_id"]),
            actor="auditor",
            payload=decision.model_dump(mode="json"),
            key=self._runtime.hmac_proposal_key,
            ttl_seconds=self._runtime.analyst_proposal_ttl_seconds,
        )
        return {"outcome_status": "success", "decision": decision, "signed_proposal": signed}

    def _escalate(self, state: _State) -> dict[str, Any]:
        _logger.warning(
            "auditor escalated to MANUAL_REVIEW",
            extra={"event": "auditor.manual_review", "reason": state.get("reason", "unspecified"),
                   "steps": state.get("step", 0)},
        )
        return {"outcome_status": "manual_review"}

    # ── routers ─────────────────────────────────────────────────────────────

    def _route_after_think(self, state: _State) -> str:
        if state.get("outcome_status") == "manual_review":
            return "escalate"
        last = state["messages"][-1]
        if last["role"] != "assistant":
            return "escalate"
        if any(b["type"] == "tool_use" for b in last["content"]):
            return "run_tools"
        if self._extract_json(last["content"]) is not None:
            return "validate_sign"
        return "validate_sign"

    def _route_after_validate(self, state: _State) -> str:
        if state.get("outcome_status") == "success":
            return "end"
        if state.get("reason"):
            return "escalate"
        return "think_act"

    # ── helpers ─────────────────────────────────────────────────────────────

    def _maybe_retry(self, state: _State, feedback: str) -> dict[str, Any]:
        retries = state["validation_retries"] + 1
        if retries > _MAX_VALIDATION_RETRIES:
            return {"validation_retries": retries, "reason": f"validation failed {retries} times: {feedback}"}
        return {
            "validation_retries": retries,
            "messages": state["messages"] + [{
                "role": "user",
                "content": [{"type": "text", "text": f"Your previous JSON was invalid: {feedback}. Retry."}],
            }],
        }

    async def _invoke_tool(self, state: _State, tu: dict[str, Any]) -> dict[str, Any]:
        try:
            domain, mcp_tool = split_tool_name(tu["name"])
        except ValueError:
            return self._tool_error(tu, f"unknown tool: {tu.get('name')!r}")

        args = dict(tu.get("input") or {})
        args["caller_sub"] = state["caller_sub"]
        args["caller_role"] = state["caller_role"]
        if domain == "documents":
            args["pipeline_id"] = state["pipeline_id"]

        _logger.info(
            "auditor tool call",
            extra={"event": "auditor.tool", "pipeline_id": state["pipeline_id"],
                   "domain": domain, "tool": mcp_tool},
        )
        try:
            result = await self._pool.call(domain, mcp_tool, args)
        except BusinessError as exc:
            return self._tool_error(tu, f"business: {exc}")
        except MCPClientError as exc:
            return self._tool_error(tu, exc.public)

        return {
            "type": "tool_result", "tool_use_id": tu["id"],
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
        }

    def _tool_error(self, tu: dict[str, Any], msg: str) -> dict[str, Any]:
        return {"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                "content": [{"type": "text", "text": msg}]}

    def _extract_json(self, blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        for b in blocks:
            if b.get("type") != "text":
                continue
            m = _JSON_BLOCK_RE.search(b.get("text", ""))
            if not m:
                continue
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
        return None
