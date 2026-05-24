"""Agent Router & Orchestration Pipeline — B3.2

Wraps the full analyst → auditor flow into ONE service-friendly entry
point: AgentRouter.process_claim(pdf_path) -> ClaimProcessingResult.

This is the single function that FastAPI (Phase C, C1.4) will call from
the POST /claims endpoint.

Architecture:
    AgentRouter
        ├── AnalystAgent + ValidationPipeline (analyst stage)
        └── AuditorAgent  + ValidationPipeline (auditor stage)

Stages are composed sequentially: the auditor receives the analyst's
TriageReport as input. If the analyst stage fails, the auditor is
skipped and a structured error is returned.

Rules applied:
    Rule 1  — ClaimProcessingResult / TriageReport / ClaimDecision schemas
              imported from packages/common/
    Rule 4  — Preserves the analyst-then-auditor pattern (READ → READ+WRITE)
    Rule 5  — Each stage wrapped in ValidationPipeline (3-retry self-healing)
    Rule 9  — Structured logging on every stage transition
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

# Make ai-orchestrator subpackages importable when running directly
_ROOT       = Path(__file__).resolve().parents[2]
_ORCH_ROOT  = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))
sys.path.insert(0, str(_ORCH_ROOT / "agents"))

from packages.common.schemas.agent import ClaimProcessingResult  # noqa: E402

from analyst_agent import AnalystAgent                           # noqa: E402
from auditor_agent import AuditorAgent                           # noqa: E402

from validation import (                                          # noqa: E402
    ValidationPipeline,
    validate_triage_report,
    validate_claim_decision,
)

# Observability — D2.x. Returns a NoopTracer when Langfuse is not
# configured, so this import is safe regardless of environment.
sys.path.insert(0, str(_ROOT))  # ensure packages root is on sys.path
from observability                  import get_tracer            # noqa: E402
# E1.2 — live event publication for the SSE "Lía está pensando" UX.
from observability.event_bus        import get_event_bus, make_event  # noqa: E402
from observability.event_bus_callback import EventBusCallbackHandler  # noqa: E402

logger = logging.getLogger(__name__)


class AgentRouter:
    """Orchestrates the analyst → auditor pipeline for a single claim.

    Both agents and their validation wrappers are created once at __init__
    so the router is cheap to call repeatedly. Dependency-inject them via
    constructor args if you need to mock during tests.
    """

    def __init__(
        self,
        analyst:          AnalystAgent | None = None,
        auditor:          AuditorAgent | None = None,
        analyst_pipeline: ValidationPipeline | None = None,
        auditor_pipeline: ValidationPipeline | None = None,
        max_attempts:     int = 3,
    ) -> None:
        self.analyst = analyst or AnalystAgent()
        self.auditor = auditor or AuditorAgent()

        self.analyst_pipeline = analyst_pipeline or ValidationPipeline(
            validator=validate_triage_report,
            agent_kind="analyst",
            max_attempts=max_attempts,
        )
        self.auditor_pipeline = auditor_pipeline or ValidationPipeline(
            validator=validate_claim_decision,
            agent_kind="auditor",
            max_attempts=max_attempts,
        )
        logger.info(
            "AgentRouter initialised | analyst_role=%s auditor_role=%s",
            self.analyst.role, self.auditor.role,
        )

    # ── Public entry point ────────────────────────────────────────

    async def process_claim(
        self,
        pdf_path: str,
        *,
        persist:        bool = True,
        trace_name:     str | None = None,
        session_id:     str | None = None,
        actor:          str | None = None,
        trace_metadata: dict[str, Any] | None = None,
    ) -> ClaimProcessingResult:
        """Run the full pipeline for one PDF and return a structured result.

        Args:
            pdf_path: Absolute path to the claim PDF.
            persist:  Unused by agents (Rule 6 — agents are READ-ONLY).
                      Kept for caller compatibility; the FastAPI route handler
                      decides whether to call create_claim after this returns.
            trace_name / session_id / actor / trace_metadata:
                Observability context (D2.x). When the configured tracer is
                Langfuse, both agents' LLM calls + tool calls end up under
                a single trace tree named `trace_name`, tagged with
                `actor` (JWT sub) and `session_id` (orchestrator
                request_id). Safe to omit — defaults yield a generic trace.

        Returns:
            ClaimProcessingResult with both stages' outputs, retry counts,
            per-stage latency, and overall pipeline_status.
        """
        logger.info("AgentRouter.process_claim | pdf_path=%s", pdf_path)
        t_start = time.perf_counter()

        # ── Observability — one handler shared by both stages ────
        # Sharing it under one trace_name lets us see analyst + auditor
        # in a single tree in the Langfuse dashboard. NoopTracer
        # returns None → handlers list stays empty, agents skip the
        # config arg entirely.
        tracer = get_tracer()
        handler = tracer.callback_handler(
            trace_name=trace_name or "claim-process",
            user_id=actor,
            session_id=session_id,
            tags=["claim-pipeline", "persist" if persist else "advisory"],
            metadata={
                "pdf_path": pdf_path,
                "persist":  persist,
                **(trace_metadata or {}),
            },
        )
        # E1.2 — also publish per-stage events to the in-memory bus so a
        # browser EventSource can show "Lía está pensando…" live. Bus
        # publishes are no-ops if session_id is missing (caller didn't
        # opt in).
        bus = get_event_bus()

        def _emit(ev_type, **kw):
            if session_id:
                bus.publish(session_id, make_event(ev_type, session_id, **kw))

        _emit(
            "pipeline.start", stage="system",
            data={"pdf_path": pdf_path, "persist": persist,
                  "trace_name": trace_name or "claim-process"},
        )

        # ── Stage 1: analyst ──────────────────────────────────────
        analyst_cb = (
            EventBusCallbackHandler(request_id=session_id, stage="analyst", bus=bus)
            if session_id else None
        )
        analyst_callbacks = [c for c in (handler, analyst_cb) if c is not None]

        _emit("agent.start", stage="analyst", name="AnalystAgent")
        a_start = time.perf_counter()
        try:
            a_state = await self.analyst_pipeline.run(
                self.analyst,
                {"pdf_path": pdf_path, "callbacks": analyst_callbacks},
            )
        except Exception as e:
            _emit("pipeline.error", stage="analyst", data={"error": str(e)})
            return self._failed(
                pdf_path,
                stage="ANALYST_FAILED",
                error=f"Analyst pipeline raised: {e}",
                analyst_ms=int((time.perf_counter() - a_start) * 1000),
                total_ms=int((time.perf_counter() - t_start) * 1000),
            )
        analyst_ms = int((time.perf_counter() - a_start) * 1000)
        _emit("agent.end", stage="analyst", name="AnalystAgent",
              latency_ms=analyst_ms,
              data={"retries": a_state.retry_count})

        if a_state.result is None or a_state.last_error:
            _emit("pipeline.error", stage="analyst",
                  data={"error": a_state.last_error or "no result"})
            return self._failed(
                pdf_path,
                stage="ANALYST_FAILED",
                error=a_state.last_error or "Analyst produced no result",
                analyst_ms=analyst_ms,
                analyst_retries=a_state.retry_count,
                total_ms=int((time.perf_counter() - t_start) * 1000),
            )

        triage_report = a_state.result
        logger.info(
            "AgentRouter | analyst OK | retries=%d status=%s flags=%d",
            a_state.retry_count, triage_report.get("status"),
            len(triage_report.get("flags") or []),
        )

        # ── Stage 2: auditor ──────────────────────────────────────
        auditor_cb = (
            EventBusCallbackHandler(request_id=session_id, stage="auditor", bus=bus)
            if session_id else None
        )
        auditor_callbacks = [c for c in (handler, auditor_cb) if c is not None]

        _emit("agent.start", stage="auditor", name="AuditorAgent")
        u_start = time.perf_counter()
        try:
            u_state = await self.auditor_pipeline.run(
                self.auditor,
                {
                    "triage_report": triage_report,
                    "callbacks":     auditor_callbacks,
                },
                # validator context — cross-reference fields against triage_report
                triage_report=triage_report,
            )
        except Exception as e:
            _emit("pipeline.error", stage="auditor", data={"error": str(e)})
            return self._failed(
                pdf_path,
                stage="AUDITOR_FAILED",
                error=f"Auditor pipeline raised: {e}",
                triage_report=triage_report,
                analyst_ms=analyst_ms,
                analyst_retries=a_state.retry_count,
                auditor_ms=int((time.perf_counter() - u_start) * 1000),
                total_ms=int((time.perf_counter() - t_start) * 1000),
            )
        auditor_ms = int((time.perf_counter() - u_start) * 1000)
        _emit("agent.end", stage="auditor", name="AuditorAgent",
              latency_ms=auditor_ms,
              data={"retries": u_state.retry_count})

        if u_state.result is None or u_state.last_error:
            _emit("pipeline.error", stage="auditor",
                  data={"error": u_state.last_error or "no result"})
            return self._failed(
                pdf_path,
                stage="AUDITOR_FAILED",
                error=u_state.last_error or "Auditor produced no result",
                triage_report=triage_report,
                analyst_ms=analyst_ms,
                analyst_retries=a_state.retry_count,
                auditor_ms=auditor_ms,
                auditor_retries=u_state.retry_count,
                total_ms=int((time.perf_counter() - t_start) * 1000),
            )

        claim_decision = u_state.result
        total_ms = int((time.perf_counter() - t_start) * 1000)

        logger.info(
            "AgentRouter | auditor OK | retries=%d decision=%s persisted_claim_id=%s "
            "total_latency_ms=%d",
            u_state.retry_count, claim_decision.get("decision"),
            claim_decision.get("persisted_claim_id"), total_ms,
        )

        _emit(
            "agent.decision", stage="auditor", name="AuditorAgent",
            data={
                "decision":          claim_decision.get("decision"),
                "persisted_claim_id": claim_decision.get("persisted_claim_id"),
                "severity_label":    triage_report.get("severity_label"),
            },
        )
        _emit(
            "pipeline.done", stage="system", latency_ms=total_ms,
            data={
                "decision":          claim_decision.get("decision"),
                "persisted_claim_id": claim_decision.get("persisted_claim_id"),
            },
        )

        # Flush so the trace lands in Langfuse before the request returns.
        # Cheap (no-op when tracer is disabled, queue-flush otherwise).
        tracer.flush()

        return ClaimProcessingResult(
            pdf_path=pdf_path,
            pipeline_status="SUCCESS",
            triage_report=triage_report,
            claim_decision=claim_decision,
            analyst_retries=a_state.retry_count,
            auditor_retries=u_state.retry_count,
            analyst_latency_ms=analyst_ms,
            auditor_latency_ms=auditor_ms,
            total_latency_ms=total_ms,
        )

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _failed(
        pdf_path:        str,
        stage:           str,
        error:           str,
        triage_report:   dict[str, Any] | None = None,
        analyst_ms:      int = 0,
        auditor_ms:      int = 0,
        analyst_retries: int = 0,
        auditor_retries: int = 0,
        total_ms:        int = 0,
    ) -> ClaimProcessingResult:
        logger.error("AgentRouter | %s | %s", stage, error)
        return ClaimProcessingResult(
            pdf_path=pdf_path,
            pipeline_status=stage,  # type: ignore[arg-type]
            triage_report=triage_report,
            claim_decision=None,
            analyst_retries=analyst_retries,
            auditor_retries=auditor_retries,
            analyst_latency_ms=analyst_ms,
            auditor_latency_ms=auditor_ms,
            total_latency_ms=total_ms,
            error_message=error,
        )
