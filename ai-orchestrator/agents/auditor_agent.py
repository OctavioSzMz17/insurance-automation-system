"""Auditor Agent — B2.3 (LangGraph implementation)

Reads the analyst's TriageReport, applies business rules, and produces
the FINAL claim decision (APPROVED / REJECTED / MANUAL_REVIEW). This is
the only agent with WRITE access — it persists its decision via the
write-restricted create_claim tool (claims MCP).

Public surface (mirrors AnalystAgent):
    AuditorAgent.role = "READ+WRITE"
    await AuditorAgent().run({"triage_report": {...}})  -> AgentState

The returned AgentState carries:
    .result            — ClaimDecision dict (with persisted_claim_id from DB)
    .reasoning_trail   — full chain-of-thought
    .last_error        — populated on failure (Rule 5 hook for B2.4)

Rules applied:
    Rule 1  — ClaimDecision schema imported from packages/common/schemas
    Rule 3  — Calls write-restricted MCP tools; analyst tools also available
    Rule 4  — READ+WRITE role; ReAct pattern via LangGraph
    Rule 5  — retry_count / last_error hooks preserved in AgentState
    Rule 8  — All keys read from .env via the factory
    Rule 9  — Structured logging on every step
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Make project-local packages importable when running directly
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))         # ./reasoning/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # ../core/

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from reasoning import AgentState                              # noqa: E402
from core.models import get_chat_model                        # noqa: E402
from core.prompts import AUDITOR_SYSTEM_PROMPT                # noqa: E402
from core.tools import AUDITOR_TOOLS                          # noqa: E402

logger = logging.getLogger(__name__)


class AuditorAgent:
    """READ + restricted WRITE agent for the final claim decision (Rule 4)."""

    role: str = "READ+WRITE"

    def __init__(self) -> None:
        """Build the LangGraph ReAct executor backed by AUDITOR_TOOLS.

        AUDITOR_TOOLS includes the read-only tools (documents + legacy) AND
        the write-restricted claims MCP tool. The analyst's tool bundle
        does NOT include the claims write tool (Rule 3 boundary).
        """
        self.model = get_chat_model()
        self.executor = create_react_agent(
            model=self.model,
            tools=AUDITOR_TOOLS,
            prompt=AUDITOR_SYSTEM_PROMPT,
        )
        logger.info(
            f"AuditorAgent initialised | role={self.role} "
            f"model={type(self.model).__name__} tools={len(AUDITOR_TOOLS)}"
        )

    # ── Public entry point ─────────────────────────────────────────

    async def run(self, agent_input: dict[str, Any]) -> AgentState:
        """Produce a final decision for a single claim.

        Args:
            agent_input: Dict with one required key:
                triage_report — the TriageReport dict produced by the analyst.

        Returns:
            AgentState. On success state.result holds a ClaimDecision dict
            and state.is_done is True. On failure state.last_error is set.
        """
        if "triage_report" not in agent_input:
            raise ValueError("AuditorAgent.run requires 'triage_report' in input")

        logger.info(f"AuditorAgent.run | input_keys={list(agent_input.keys())}")
        state = AgentState(claim_input=agent_input)
        state.iteration = 1
        state.log_step("start", input_keys=list(agent_input.keys()))

        try:
            # If the validation pipeline (B2.4) is retrying us, include the
            # specific violations so the LLM can self-correct.
            retry_block = ""
            if agent_input.get("previous_attempt_error"):
                pae = agent_input["previous_attempt_error"]
                retry_block = (
                    "\n\nPREVIOUS ATTEMPT FAILED VALIDATION (attempt "
                    f"{pae.get('attempt')}). You MUST address these issues:\n- "
                    + "\n- ".join(pae.get("violations", []))
                )
            # When `persist=False` (used by re-analysis), the auditor must
            # NOT call create_claim — we only want a fresh recommendation
            # against current data without inserting a duplicate row.
            persist = agent_input.get("persist", True)
            persist_clause = (
                "call create_claim to persist the result, "
                if persist
                else "DO NOT call create_claim (this is an advisory re-analysis), "
            )
            user_msg = HumanMessage(
                content=(
                    "Review the TriageReport below and produce the final claim decision. "
                    f"Apply the decision rules from your system prompt, {persist_clause}"
                    "then respond with the final JSON object.\n\n"
                    "TRIAGE REPORT:\n"
                    + json.dumps(agent_input["triage_report"], ensure_ascii=False, indent=2)
                    + retry_block
                )
            )
            graph_state = await self.executor.ainvoke({"messages": [user_msg]})
            self._populate_state_from_graph(state, graph_state)

        except Exception as e:
            state.last_error = f"Auditor run failed: {e}"
            state.log_step("error", error=str(e))
            logger.error(state.last_error, exc_info=True)
            return state

        return state

    # ── Internal helpers ───────────────────────────────────────────

    def _populate_state_from_graph(
        self, state: AgentState, graph_state: dict[str, Any]
    ) -> None:
        """Translate LangGraph's output into our AgentState container.

        IMPORTANT: persisted_claim_id is always overwritten with the REAL
        value from the create_claim tool result (or set to None if the
        agent never actually called the tool). This prevents the LLM from
        hallucinating a fake claim_id.
        """
        messages = graph_state.get("messages", [])
        real_persisted_claim_id: int | None = None

        for msg in messages:
            msg_type = type(msg).__name__
            content  = getattr(msg, "content", "")
            tool_calls = getattr(msg, "tool_calls", None)

            if tool_calls:
                for tc in tool_calls:
                    state.log_step(
                        "tool_call",
                        tool=tc.get("name"),
                        arguments=tc.get("args"),
                    )
            elif msg_type == "ToolMessage":
                tool_name = getattr(msg, "name", None)
                state.log_step("tool_result", tool=tool_name, content=content)
                state.observations.append({"tool": tool_name, "result": content})
                # Capture the REAL persisted claim_id from the tool result
                if tool_name == "create_claim":
                    real_persisted_claim_id = self._extract_real_claim_id(content)
            elif msg_type == "AIMessage" and content:
                state.log_step("ai_message", content=content)

        # Final result = last AI message, parsed as JSON
        final_text = ""
        for msg in reversed(messages):
            if type(msg).__name__ == "AIMessage" and getattr(msg, "content", ""):
                final_text = msg.content
                break

        decision = self._parse_final_decision(final_text)
        if decision is None:
            state.last_error = "Auditor did not produce a parseable ClaimDecision"
            state.log_step("error", error=state.last_error, raw_output=final_text[:500])
            return

        # OVERWRITE persisted_claim_id with the real value from the tool call.
        # If create_claim wasn't actually called, this becomes None — and the
        # validator will flag it on the retry.
        decision["persisted_claim_id"] = real_persisted_claim_id

        state.result  = decision
        state.is_done = True
        state.log_step(
            "done",
            decision=decision.get("decision"),
            persisted_claim_id=real_persisted_claim_id,
        )

    @staticmethod
    def _extract_real_claim_id(tool_result_text: Any) -> int | None:
        """Pull claim_id out of the create_claim tool's JSON result."""
        if not tool_result_text:
            return None
        try:
            data = json.loads(tool_result_text) if isinstance(tool_result_text, str) else tool_result_text
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict) or "error" in data:
            return None
        claim_id = data.get("claim_id")
        return int(claim_id) if claim_id is not None else None

    @staticmethod
    def _parse_final_decision(text: str) -> dict[str, Any] | None:
        """Extract the JSON object from the agent's final message.

        Tolerates models that wrap JSON in markdown fences or add prose.
        """
        if not text:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse auditor JSON output: {e}")
            return None
