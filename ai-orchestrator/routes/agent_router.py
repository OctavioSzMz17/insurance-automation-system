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
        persist: bool = True,
    ) -> ClaimProcessingResult:
        """Run the full pipeline for one PDF and return a structured result.

        Args:
            pdf_path: Absolute path to the claim PDF.
            persist:  When True (default), the auditor is instructed to call
                      the `create_claim` MCP tool and a new claim row is
                      written. When False, both agents run to completion
                      and produce a recommendation, but **no DB write
                      happens** — useful for re-analysis of an existing
                      claim where we only want fresh reasoning.

        Returns:
            ClaimProcessingResult with both stages' outputs, retry counts,
            per-stage latency, and overall pipeline_status.
        """
        logger.info("AgentRouter.process_claim | pdf_path=%s", pdf_path)
        t_start = time.perf_counter()

        # ── Stage 1: analyst ──────────────────────────────────────
        a_start = time.perf_counter()
        try:
            a_state = await self.analyst_pipeline.run(
                self.analyst, {"pdf_path": pdf_path}
            )
        except Exception as e:
            return self._failed(
                pdf_path,
                stage="ANALYST_FAILED",
                error=f"Analyst pipeline raised: {e}",
                analyst_ms=int((time.perf_counter() - a_start) * 1000),
                total_ms=int((time.perf_counter() - t_start) * 1000),
            )
        analyst_ms = int((time.perf_counter() - a_start) * 1000)

        if a_state.result is None or a_state.last_error:
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
        u_start = time.perf_counter()
        try:
            u_state = await self.auditor_pipeline.run(
                self.auditor,
                {"triage_report": triage_report, "persist": persist},
                triage_report=triage_report,  # cross-reference context for validator
            )
        except Exception as e:
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

        if u_state.result is None or u_state.last_error:
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
