"""Analyst Agent — B2.2 (LangGraph implementation)

READ-ONLY agent that gathers facts about an incoming claim by calling
MCP tools, then produces a structured TriageReport. The agent's
reasoning is driven by a real LLM (Claude or OpenAI, with optional
local→external fallback) via LangGraph's prebuilt ReAct executor.

Public surface (unchanged from B2.1):
    AnalystAgent.role  = "READ-ONLY"
    await AnalystAgent().run({"pdf_path": "..."})  -> AgentState

The returned AgentState has:
    .result            — TriageReport as a dict
    .reasoning_trail   — full chain-of-thought (every LLM + tool message)
    .last_error        — set when something fails (Rule 5 hook for B2.4)

Rules applied:
    Rule 1  — TriageReport / ClaimFlag imported from packages/common/schemas
    Rule 3  — Calls MCPs only; never touches DB directly
    Rule 4  — READ-ONLY role; ReAct pattern via LangGraph
    Rule 5  — retry_count / last_error hooks preserved in AgentState
    Rule 8  — All API keys read from .env via the factory
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # exposes ./reasoning/

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from reasoning import AgentState  # noqa: E402

# core/ is at ../core relative to this file; add it to sys.path so plain
# imports work without making ai-orchestrator a proper package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.models import get_chat_model                   # noqa: E402
from core.prompts import ANALYST_SYSTEM_PROMPT            # noqa: E402
from core.tools import ALL_TOOLS                          # noqa: E402

logger = logging.getLogger(__name__)


class AnalystAgent:
    """READ-ONLY agent for claim extraction and triage (Rule 4)."""

    role: str = "READ-ONLY"

    def __init__(self) -> None:
        """Build the underlying LangGraph ReAct executor.

        Reads LLM_PROVIDER / MODEL_NAME / LLM_PROVIDER_API_KEY from .env
        via core.models.get_chat_model().
        """
        self.model = get_chat_model()
        self.executor = create_react_agent(
            model=self.model,
            tools=ALL_TOOLS,
            prompt=ANALYST_SYSTEM_PROMPT,
        )
        logger.info(f"AnalystAgent initialised | role={self.role} model={type(self.model).__name__}")

    # ── Public entry point ─────────────────────────────────────────

    async def run(self, claim_input: dict[str, Any]) -> AgentState:
        """Triage a single claim.

        Args:
            claim_input: Dict with at minimum {"pdf_path": "..."}.

        Returns:
            AgentState. On success state.result holds a TriageReport dict and
            state.is_done is True. On failure state.last_error is populated.
        """
        if "pdf_path" not in claim_input:
            raise ValueError("AnalystAgent.run requires 'pdf_path' in claim_input")

        logger.info(f"AnalystAgent.run | claim_input={claim_input}")
        state = AgentState(claim_input=claim_input)
        state.iteration = 1
        state.log_step("start", input=claim_input)

        try:
            # If the validation pipeline (B2.4) is retrying us, include the
            # specific violations so the LLM can self-correct.
            retry_block = ""
            if claim_input.get("previous_attempt_error"):
                pae = claim_input["previous_attempt_error"]
                retry_block = (
                    "\n\nPREVIOUS ATTEMPT FAILED VALIDATION (attempt "
                    f"{pae.get('attempt')}). You MUST address these issues:\n- "
                    + "\n- ".join(pae.get("violations", []))
                )
            user_msg = HumanMessage(
                content=f"Triage the claim at pdf_path: {claim_input['pdf_path']}" + retry_block
            )

            # D2.2 — forward Langfuse / OTel callbacks if the orchestrator
            # injected any (`callbacks` key in the input dict). Falsy →
            # LangGraph runs with no extra observers (zero overhead).
            callbacks = claim_input.get("callbacks") or []
            invoke_kwargs: dict[str, Any] = {}
            if callbacks:
                invoke_kwargs["config"] = {"callbacks": callbacks}

            graph_state = await self.executor.ainvoke(
                {"messages": [user_msg]}, **invoke_kwargs,
            )
            self._populate_state_from_graph(state, graph_state)

        except Exception as e:
            state.last_error = f"Agent run failed: {e}"
            state.log_step("error", error=str(e))
            logger.error(state.last_error, exc_info=True)
            return state

        return state

    # ── Internal helpers ───────────────────────────────────────────

    def _populate_state_from_graph(
        self, state: AgentState, graph_state: dict[str, Any]
    ) -> None:
        """Translate LangGraph's output into our AgentState container."""
        messages = graph_state.get("messages", [])

        # Build the reasoning trail from the message stream
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
                state.log_step("tool_result", tool=getattr(msg, "name", None), content=content)
                # Track tool results as observations
                state.observations.append({"tool": getattr(msg, "name", None), "result": content})
            elif msg_type == "AIMessage" and content:
                state.log_step("ai_message", content=content)

        # Final result = last AI message, parsed as JSON
        final_text = ""
        for msg in reversed(messages):
            if type(msg).__name__ == "AIMessage" and getattr(msg, "content", ""):
                final_text = msg.content
                break

        report = self._parse_final_report(final_text)
        if report is None:
            state.last_error = "Agent did not produce a parseable TriageReport"
            state.log_step("error", error=state.last_error, raw_output=final_text[:500])
            return

        state.result  = report
        state.is_done = True
        state.log_step("done", status=report.get("status"))

    @staticmethod
    def _parse_final_report(text: str) -> dict[str, Any] | None:
        """Extract the JSON object from the agent's final message.

        Tolerates models that wrap JSON in markdown fences or add prose.
        Returns None if no parseable JSON object is found.
        """
        if not text:
            return None
        # Strip code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        # Find the outermost JSON object
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse agent JSON output: {e}")
            return None
