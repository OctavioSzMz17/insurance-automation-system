"""Analyst agent — LangGraph ReAct implementation.

Graph topology:

    START
      ↓
    think_act ────────────────────────────────────────────┐
      ├─ assistant emits tool_use blocks  → run_tools ────┘
      ├─ assistant emits final JSON       → validate
      │                                       ├─ ok                → sign → END (success)
      │                                       ├─ retries left      → think_act (with feedback)
      │                                       └─ retries exhausted → escalate → END (manual_review)
      └─ neither tool_use nor JSON        → escalate → END (manual_review)

    timeout / max_steps are checked at the top of think_act → escalate.

Invariants (security-critical, mirrored from the previous hand-rolled loop):
- caller_sub / caller_role overwritten on EVERY tool call (Rule 7).
- documents tools always receive the run's pipeline_id (cannot be influenced by LLM).
- Severity is overwritten deterministically by validate_triage_payload (Rule 4).
- Write-restricted tools are not exposed by core.tools.build_tool_specs (Rule 4).
- Validation feedback is bounded by MAX_VALIDATION_RETRIES → MANUAL_REVIEW (Rule 5).
- Wall-clock timeout + max_steps escalate to MANUAL_REVIEW (Rules 5, 6).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from observability import Span, SpanKind, get_tracer
from packages.common.exceptions import (
    BusinessError,
    ForbiddenError,
    MCPClientError,
    ValidationFailure,
)
from packages.common.schemas.claim import ClaimProposal
from packages.common.schemas.triage import TriageReport

from core import proposal as proposal_mod
from core.config_runtime import OrchestratorRuntimeView
from core.logging_bridge import get_logger
from core.mcp_client import MCPClientPool
from core.models import LLMClient
from core.prompts.analyst import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION
from core.tools import build_tool_specs, dispatch, split_tool_name
from core.validation import MAX_VALIDATION_RETRIES, validate_triage_payload

_logger = get_logger(__name__)
# Tolerant fence matcher: accepts ```json … ```, ```JSON … ``` or a bare ``` … ```
# fence. The language tag is optional because smaller models routinely drop it.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _describe_turn(content_blocks: list[dict[str, Any]]) -> str:
    """One-line summary of what the model did this step, for the trace.

    Cheap to compute and it is the field that makes a trace readable at a glance:
    "called documents__parse_pdf" tells you more than a token count.
    """
    tools = [b.get("name") for b in content_blocks if b.get("type") == "tool_use"]
    if tools:
        return "called " + ", ".join(str(t) for t in tools)
    if any(b.get("type") == "text" for b in content_blocks):
        return "emitted text"
    return "emitted nothing"


def _iter_balanced_objects(text: str):
    """Yield top-level {...} substrings via brace matching.

    Last-resort fallback when the model emits raw JSON with no code fence.
    Brace counting ignores braces inside strings is *not* attempted — each
    candidate is validated with json.loads by the caller, so a miscount simply
    fails to parse and the next candidate is tried.
    """
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                yield text[start : i + 1]
                start = -1


@dataclass(frozen=True)
class RunContext:
    pipeline_id: UUID
    caller_sub: str
    caller_role: str
    # The client the claim is filed on behalf of. None → caller is the claimant.
    claimant_sub: str | None = None


@dataclass
class AnalystOutcome:
    status: Literal["success", "manual_review"]
    triage: TriageReport | None = None
    proposal: ClaimProposal | None = None
    reason: str | None = None
    steps_used: int = 0


# ── Graph state ──────────────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    # Static run identity (set in __init__ payload, never mutated by nodes)
    pipeline_id: str          # UUID stringified to keep state JSON-serialisable
    caller_sub: str
    caller_role: str
    claimant_sub: str         # the client the claim is filed for (defaults to caller_sub)

    # Loop bookkeeping
    messages: list[dict[str, Any]]
    step: int
    deadline: float
    validation_retries: int

    # Terminal payload (typed objects, not dicts — TriageReport is strict=True and would
    # not survive a list→tuple roundtrip if re-validated from a JSON dict).
    outcome_status: Literal["success", "manual_review"]
    triage: TriageReport | None
    proposal: ClaimProposal | None
    reason: str | None


# ── Analyst ──────────────────────────────────────────────────────────────────


class Analyst:
    """LangGraph-backed analyst. The compiled graph is built once at construction."""

    def __init__(
        self,
        *,
        runtime: OrchestratorRuntimeView,
        pool: MCPClientPool,
        llm: LLMClient,
    ) -> None:
        self._runtime = runtime
        self._pool = pool
        self._llm = llm
        self._tool_specs = build_tool_specs()
        self._graph = self._build_graph()

    # ── public ──────────────────────────────────────────────────────────────

    async def run(self, ctx: RunContext) -> AnalystOutcome:
        claimant = ctx.claimant_sub or ctx.caller_sub
        initial: AgentState = {
            "pipeline_id": str(ctx.pipeline_id),
            "caller_sub": ctx.caller_sub,
            "caller_role": ctx.caller_role,
            "claimant_sub": claimant,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": (
                        f"pipeline_id: {ctx.pipeline_id}\n"
                        f"caller_sub (operator running this analysis): {ctx.caller_sub}\n"
                        f"caller_role: {ctx.caller_role}\n"
                        f"claimant_sub (the CLIENT this claim is filed on behalf of): {claimant}\n"
                        "Begin by calling documents__parse_pdf with this pipeline_id, then "
                        "resolve the CLAIMANT's policies (look up claimant_sub, not caller_sub)."
                    ),
                }],
            }],
            "step": 0,
            "deadline": time.monotonic() + self._runtime.analyst_timeout_seconds,
            "validation_retries": 0,
        }
        async with get_tracer().span(
            "analyst.run",
            pipeline_id=str(ctx.pipeline_id),
            claimant_sub=claimant,
            max_steps=self._runtime.analyst_max_steps,
            timeout_seconds=self._runtime.analyst_timeout_seconds,
            model=getattr(self._llm, "model", "unknown"),
        ) as span:
            # recursion_limit must allow think_act → run_tools → think_act loops; we give it room.
            final: AgentState = await self._graph.ainvoke(
                initial, config={"recursion_limit": self._runtime.analyst_max_steps * 3 + 10}
            )
            status = final.get("outcome_status", "manual_review")
            steps_used = final.get("step", 0)
            span.set_attributes(outcome=status, steps_used=steps_used)

            if status == "success":
                triage = final.get("triage")
                if triage is not None:
                    span.set_attributes(
                        severity=triage.severity.value,
                        policy_id=str(triage.policy_id),
                        reported_damage_amount=str(triage.reported_damage_amount),
                    )
                return AnalystOutcome(
                    status="success",
                    triage=triage,
                    proposal=final.get("proposal"),
                    steps_used=steps_used,
                )

            reason = final.get("reason", "unspecified")
            span.set_status(False, message=reason)
            return AnalystOutcome(status="manual_review", reason=reason, steps_used=steps_used)

    # ── graph construction ─────────────────────────────────────────────────

    def _build_graph(self):
        g: StateGraph = StateGraph(AgentState)
        g.add_node("think_act", self._node_think_act)
        g.add_node("run_tools", self._node_run_tools)
        g.add_node("validate_sign", self._node_validate_sign)
        g.add_node("escalate", self._node_escalate)

        g.add_edge(START, "think_act")
        g.add_conditional_edges(
            "think_act",
            self._route_after_think,
            {"run_tools": "run_tools", "validate_sign": "validate_sign", "escalate": "escalate"},
        )
        g.add_edge("run_tools", "think_act")
        g.add_conditional_edges(
            "validate_sign",
            self._route_after_validate,
            {"think_act": "think_act", "end": END, "escalate": "escalate"},
        )
        g.add_edge("escalate", END)
        return g.compile()

    # ── nodes ───────────────────────────────────────────────────────────────

    async def _node_think_act(self, state: AgentState) -> dict[str, Any]:
        # Limits checked here so they apply before any LLM token is spent on this step.
        if time.monotonic() > state["deadline"]:
            return {"outcome_status": "manual_review", "reason": f"timeout after {state['step']} steps"}
        if state["step"] >= self._runtime.analyst_max_steps:
            return {"outcome_status": "manual_review", "reason": f"reached max_steps={self._runtime.analyst_max_steps}"}

        async with get_tracer().span(
            "analyst.think_act",
            kind=SpanKind.GENERATION,
            step=state["step"] + 1,
            pipeline_id=state["pipeline_id"],
        ) as span:
            turn = await self._llm.complete(
                system=SYSTEM_PROMPT, messages=state["messages"], tools=self._tool_specs
            )
            span.set_usage(
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                model=getattr(self._llm, "model", "unknown"),
            )
            span.set_attributes(
                stop_reason=turn.stop_reason,
                emitted=_describe_turn(turn.content_blocks),
                prompt_version=SYSTEM_PROMPT_VERSION,
            )

        _logger.info(
            "analyst step",
            extra={
                "event": "analyst.step",
                "pipeline_id": state["pipeline_id"],
                "step": state["step"] + 1,
                "stop_reason": turn.stop_reason,
            },
        )
        return {
            "messages": state["messages"] + [{"role": "assistant", "content": turn.content_blocks}],
            "step": state["step"] + 1,
        }

    async def _node_run_tools(self, state: AgentState) -> dict[str, Any]:
        # The most recent assistant message holds the tool_use blocks we're about to dispatch.
        last = state["messages"][-1]
        tool_uses = [b for b in last["content"] if b["type"] == "tool_use"]
        results: list[dict[str, Any]] = []
        for tu in tool_uses:
            results.append(await self._invoke_tool(state, tu))
        return {"messages": state["messages"] + [{"role": "user", "content": results}]}

    async def _node_validate_sign(self, state: AgentState) -> dict[str, Any]:
        async with get_tracer().span(
            "analyst.validate",
            pipeline_id=state["pipeline_id"],
            retries_so_far=state["validation_retries"],
        ) as span:
            return self._validate_sign(state, span)

    def _validate_sign(self, state: AgentState, span: Span) -> dict[str, Any]:
        last = state["messages"][-1]
        payload = self._extract_json(last["content"])
        if payload is None:
            # The model ended without an extractable JSON report. Give it a bounded
            # number of chances to re-emit cleanly before escalating — small models
            # sometimes drop the fence on the first try.
            retries = state["validation_retries"] + 1
            span.set_status(False, message="no structured report in the reply")
            span.set_attributes(outcome="no_json", retries=retries)
            if retries > MAX_VALIDATION_RETRIES:
                return {
                    "validation_retries": retries,
                    "reason": "analyst could not produce a structured report",
                }
            return {
                "validation_retries": retries,
                "messages": state["messages"] + [{
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": (
                            "I could not find a JSON report in your reply. Emit your final "
                            "answer as a SINGLE fenced ```json {...} ``` block and nothing else."
                        ),
                    }],
                }],
            }

        try:
            triage = validate_triage_payload(payload, pipeline_id=UUID(state["pipeline_id"]))
        except ValidationFailure as exc:
            retries = state["validation_retries"] + 1
            span.set_status(False, message=str(exc))
            span.set_attributes(outcome="invalid", retries=retries)
            if retries > MAX_VALIDATION_RETRIES:
                return {
                    "validation_retries": retries,
                    "reason": f"validation failed {retries} times: {exc}",
                }
            return {
                "validation_retries": retries,
                "messages": state["messages"] + [{
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Your previous JSON failed validation: {exc}. "
                            "Retry, using tools again if needed."
                        ),
                    }],
                }],
            }

        # The containment, made visible: whatever severity the model asked for, the
        # stored one comes from the amount. Both land in the trace side by side.
        claimed = payload.get("severity")
        span.set_attributes(
            outcome="signed",
            severity_claimed=str(claimed) if claimed is not None else None,
            severity_computed=triage.severity.value,
            severity_overridden=claimed is not None and str(claimed) != triage.severity.value,
            pipeline_id_claimed=str(payload.get("pipeline_id") or "") or None,
            evidence_quotes=len(triage.evidence_quotes),
        )

        signed = proposal_mod.build_proposal(
            pipeline_id=UUID(state["pipeline_id"]),
            actor="analyst",
            payload=triage.model_dump(mode="json"),
            key=self._runtime.hmac_proposal_key,
            ttl_seconds=self._runtime.analyst_proposal_ttl_seconds,
        )
        return {
            "outcome_status": "success",
            "triage": triage,
            "proposal": signed,
        }

    async def _node_escalate(self, state: AgentState) -> dict[str, Any]:
        reason = state.get("reason", "unspecified")
        async with get_tracer().span(
            "analyst.escalate",
            kind=SpanKind.EVENT,
            pipeline_id=state["pipeline_id"],
            steps=state.get("step", 0),
        ) as span:
            span.set_status(False, message=reason)
        _logger.warning(
            "analyst escalated to MANUAL_REVIEW",
            extra={
                "event": "analyst.manual_review",
                "reason": reason,
                "steps": state.get("step", 0),
            },
        )
        return {"outcome_status": "manual_review"}

    # ── routers ─────────────────────────────────────────────────────────────

    def _route_after_think(self, state: AgentState) -> str:
        # If think_act already produced a terminal status (timeout/max_steps), escalate.
        if state.get("outcome_status") == "manual_review":
            return "escalate"
        last = state["messages"][-1]
        if last["role"] != "assistant":
            return "escalate"
        if any(b["type"] == "tool_use" for b in last["content"]):
            return "run_tools"
        if self._extract_json(last["content"]) is not None:
            return "validate_sign"
        # Free text without tools and without JSON — treat as escalation hint.
        return "validate_sign"  # validate_sign returns 'reason' which routes to escalate

    def _route_after_validate(self, state: AgentState) -> str:
        if state.get("outcome_status") == "success":
            return "end"
        if state.get("reason"):
            return "escalate"
        return "think_act"

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _invoke_tool(self, state: AgentState, tu: dict[str, Any]) -> dict[str, Any]:
        try:
            domain, mcp_tool = split_tool_name(tu["name"])
        except ValueError:
            return self._tool_error(tu, f"unknown tool: {tu.get('name')!r}")

        args = dict(tu.get("input") or {})
        # Rule 7: caller identity is server-controlled, NEVER LLM-controlled.
        args["caller_sub"] = state["caller_sub"]
        args["caller_role"] = state["caller_role"]
        if domain == "documents":
            args["pipeline_id"] = state["pipeline_id"]

        _logger.info(
            "analyst tool call",
            extra={"event": "analyst.tool", "pipeline_id": state["pipeline_id"],
                   "domain": domain, "tool": mcp_tool},
        )
        async with get_tracer().span(
            f"tool.{domain}.{mcp_tool}",
            kind=SpanKind.TOOL,
            domain=domain,
            tool=mcp_tool,
            pipeline_id=state["pipeline_id"],
            # The identity on this call was imposed by the server, whatever the model
            # asked for (Rule 7). Recorded so the trace shows the invariant holding.
            identity_forced=True,
            caller_sub=state["caller_sub"],
            caller_role=state["caller_role"],
        ) as span:
            span.set_input({k: v for k, v in args.items()
                            if k not in {"caller_sub", "caller_role"}})
            try:
                # Routed through the registry, never straight at the pool: dispatch is what
                # enforces the write gate (Rule 4) and rejects names that are not registered
                # tools. Hiding write tools from the tool spec is the first barrier; this is
                # the second, and it is the one that holds when the model invents a name.
                result = await dispatch(self._pool, tu["name"], args, allow_writes=False)
            except ForbiddenError as exc:
                _logger.warning(
                    "analyst attempted a write-restricted tool",
                    extra={"event": "analyst.write_refused", "pipeline_id": state["pipeline_id"],
                           "tool": tu["name"]},
                )
                span.set_status(False, message=f"write refused: {exc}")
                span.set_attributes(write_refused=True)
                return self._tool_error(tu, f"forbidden: {exc}")
            except ValueError as exc:
                span.set_status(False, message=f"unknown tool: {exc}")
                return self._tool_error(tu, f"unknown tool: {exc}")
            except BusinessError as exc:
                span.set_status(False, message=f"business: {exc}")
                return self._tool_error(tu, f"business: {exc}")
            except MCPClientError as exc:
                span.set_status(False, message=exc.public)
                return self._tool_error(tu, exc.public)

            span.set_output(result)

        return {
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
        }

    def _tool_error(self, tu: dict[str, Any], msg: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "is_error": True,
            "content": [{"type": "text", "text": msg}],
        }

    def _extract_json(self, blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Extract the final triage object, tolerant of how the model framed it.

        Order of attempts (most explicit → most lenient):
          1. A fenced ```json … ``` / ``` … ``` block.
          2. Any balanced {...} object found anywhere in the text.
        Only dict payloads are accepted; lists/scalars are ignored.
        """
        for b in blocks:
            if b.get("type") != "text":
                continue
            text = b.get("text", "")

            # 1. Fenced block(s).
            for m in _FENCED_JSON_RE.finditer(text):
                obj = self._loads_obj(m.group(1))
                if obj is not None:
                    return obj

            # 2. Bare JSON object(s) — try the longest candidate first so a full
            #    report wins over a nested fragment.
            for candidate in sorted(_iter_balanced_objects(text), key=len, reverse=True):
                obj = self._loads_obj(candidate)
                if obj is not None:
                    return obj
        return None

    @staticmethod
    def _loads_obj(raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
