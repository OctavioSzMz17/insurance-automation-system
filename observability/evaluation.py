"""Eval harness — Phase D6.1.

Runs Lía against a curated golden set and reports accuracy + cost
metrics. Catches prompt regressions before they ship.

Run inside the orchestrator container (it already has the agent stack,
LLM keys, and Langfuse keys):

    docker exec insureai-orchestrator python -m observability.evaluation list
    docker exec insureai-orchestrator python -m observability.evaluation run
    docker exec insureai-orchestrator python -m observability.evaluation run --case case_001_clean_approval
    docker exec insureai-orchestrator python -m observability.evaluation run --json > evals_out.json

Each eval:
    - calls agent_router.process_claim(persist=False) → no DB writes
    - emits a Langfuse trace named "eval-{case_id}" so dashboards can
      separate evals from real claims
    - compares decision / severity / flag substrings vs the case's
      expected.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Make orchestrator package importable when run as `python -m observability.evaluation`.
_HERE   = Path(__file__).resolve().parent
_ROOT   = _HERE.parent
_ORCH   = _ROOT / "ai-orchestrator"
for p in (str(_ROOT), str(_ORCH), str(_ORCH / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

_GOLDEN = _HERE / "evals" / "golden"

logger = logging.getLogger("evaluation")
report_logger = logging.getLogger("evaluation.report")


# ── Data shapes ──────────────────────────────────────────────────

@dataclass
class CaseExpectation:
    decision:      Optional[str] = None
    severity:      Optional[str] = None
    must_flag:     list[str]     = field(default_factory=list)
    must_not_flag: list[str]     = field(default_factory=list)


@dataclass
class GoldenCase:
    case_id:  str
    pdf_path: Path
    name:     str
    notes:    str
    expected: CaseExpectation


@dataclass
class CaseResult:
    case_id:          str
    name:             str
    passed:           bool
    decision_match:   bool
    severity_match:   bool
    flag_failures:    list[str]
    actual_decision:  Optional[str]
    actual_severity:  Optional[str]
    pipeline_status:  Optional[str]
    error:            Optional[str]
    latency_ms:       int


# ── Loader ───────────────────────────────────────────────────────

def load_cases() -> list[GoldenCase]:
    if not _GOLDEN.exists():
        return []
    cases: list[GoldenCase] = []
    for case_dir in sorted(_GOLDEN.iterdir()):
        if not case_dir.is_dir():
            continue
        manifest_path = case_dir / "expected.json"
        pdf_path      = case_dir / "claim.pdf"
        if not manifest_path.exists() or not pdf_path.exists():
            logger.warning(
                "skipping %s — missing expected.json or claim.pdf",
                case_dir.name,
            )
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        exp = m.get("expected") or {}
        cases.append(GoldenCase(
            case_id=case_dir.name,
            pdf_path=pdf_path,
            name=m.get("name") or case_dir.name,
            notes=m.get("notes") or "",
            expected=CaseExpectation(
                decision=exp.get("decision"),
                severity=exp.get("severity"),
                must_flag=list(exp.get("must_flag") or []),
                must_not_flag=list(exp.get("must_not_flag") or []),
            ),
        ))
    return cases


# ── Runner ───────────────────────────────────────────────────────

_router_singleton = None  # AgentRouter is heavy to build, reuse across cases


def _get_router():
    global _router_singleton
    if _router_singleton is None:
        from config import get_settings
        from routes.agent_router import AgentRouter
        _router_singleton = AgentRouter(
            max_attempts=get_settings().max_retry_attempts,
        )
    return _router_singleton


async def run_case(case: GoldenCase) -> CaseResult:
    router = _get_router()
    t0 = time.perf_counter()
    error: Optional[str] = None
    actual_decision: Optional[str] = None
    actual_severity: Optional[str] = None
    pipeline_status: Optional[str] = None
    reasoning_blob:  str = ""

    try:
        result = await router.process_claim(
            str(case.pdf_path),
            persist=False,
            trace_name=f"eval-{case.case_id}",
            actor="eval-harness",
            trace_metadata={
                "eval_case": case.case_id,
                "eval_name": case.name,
            },
        )
        pipeline_status = result.pipeline_status
        if result.claim_decision:
            actual_decision = result.claim_decision.get("decision")
            reasoning_blob += " " + (
                result.claim_decision.get("rationale")
                or result.claim_decision.get("notes")
                or ""
            )
        if result.triage_report:
            actual_severity = result.triage_report.get("severity_label")
            # Flags can be either strings or dicts ({code, message});
            # coerce both to text so substring asserts see everything.
            for f in (result.triage_report.get("flags") or []):
                if isinstance(f, dict):
                    reasoning_blob += " " + " ".join(str(v) for v in f.values())
                else:
                    reasoning_blob += " " + str(f)
            reasoning_blob += " " + (result.triage_report.get("summary") or "")
        error = result.error_message
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Compare
    decision_match = (
        case.expected.decision is None
        or case.expected.decision == actual_decision
    )
    severity_match = (
        case.expected.severity is None
        or case.expected.severity == actual_severity
    )
    reasoning_lower = reasoning_blob.lower()
    flag_failures: list[str] = []
    for f in case.expected.must_flag:
        if f.lower() not in reasoning_lower:
            flag_failures.append(f"expected flag missing: {f!r}")
    for f in case.expected.must_not_flag:
        if f.lower() in reasoning_lower:
            flag_failures.append(f"unexpected flag present: {f!r}")

    passed = (
        error is None
        and decision_match
        and severity_match
        and not flag_failures
    )
    return CaseResult(
        case_id=case.case_id,
        name=case.name,
        passed=passed,
        decision_match=decision_match,
        severity_match=severity_match,
        flag_failures=flag_failures,
        actual_decision=actual_decision,
        actual_severity=actual_severity,
        pipeline_status=pipeline_status,
        error=error,
        latency_ms=elapsed_ms,
    )


async def run_all(case_filter: Optional[str] = None) -> list[CaseResult]:
    cases = load_cases()
    if case_filter:
        cases = [c for c in cases if c.case_id == case_filter]
    if not cases:
        logger.warning("No cases matched | filter=%r", case_filter)
        return []
    logger.info("Running %d eval(s) against current prompts", len(cases))
    logger.info("%s", "─" * 60)
    results: list[CaseResult] = []
    for c in cases:
        logger.info("Running case | case_id=%s name=%s", c.case_id, c.name)
        r = await run_case(c)
        logger.info(
            "Case result | case_id=%s passed=%s latency_ms=%d",
            c.case_id, r.passed, r.latency_ms,
        )
        results.append(r)
    return results


# ── Reporting ────────────────────────────────────────────────────

def print_report(results: list[CaseResult]) -> None:
    if not results:
        report_logger.info("No results.")
        return
    report_logger.info("=" * 60)
    total          = len(results)
    decision_ok    = sum(1 for r in results if r.decision_match)
    severity_ok    = sum(1 for r in results if r.severity_match)
    passed         = sum(1 for r in results if r.passed)
    avg_latency_ms = sum(r.latency_ms for r in results) / total
    pct            = 100.0 * passed / total
    report_logger.info("DECISION ACCURACY | value=%d/%d", decision_ok, total)
    report_logger.info("SEVERITY ACCURACY | value=%d/%d", severity_ok, total)
    report_logger.info("OVERALL PASSED | value=%d/%d pct=%.1f", passed, total, pct)
    report_logger.info("AVG LATENCY | seconds=%.1f", avg_latency_ms / 1000.0)

    failed = [r for r in results if not r.passed]
    if failed:
        report_logger.warning("FAILURES")
        report_logger.warning("%s", "-" * 60)
        for r in failed:
            report_logger.warning("Case failed | case_id=%s name=%s", r.case_id, r.name)
            if r.error:
                report_logger.warning("pipeline_error=%s", r.error)
                continue
            if not r.decision_match:
                report_logger.warning(
                    "decision_mismatch | actual_decision=%r",
                    r.actual_decision,
                )
            if not r.severity_match:
                report_logger.warning(
                    "severity_mismatch | actual_severity=%r",
                    r.actual_severity,
                )
            for f in r.flag_failures:
                report_logger.warning("flag_failure=%s", f)


# ── CLI ──────────────────────────────────────────────────────────

def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="observability.evaluation",
        description="Eval harness for Lía",
    )
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_run = subs.add_parser("run", help="run evals against current prompts")
    p_run.add_argument("--case", help="run a single case by directory id")
    p_run.add_argument("--json", action="store_true",
                       help="emit machine-readable JSON instead of report")

    subs.add_parser("list", help="list available golden cases")

    args = parser.parse_args()

    # Configure observability logging the same way the API does — so eval
    # runs land in Langfuse exactly like real claims.
    from observability.logging import configure_logging
    configure_logging(
        level=os.getenv("LOG_LEVEL", "INFO"),
        log_format="text",
    )

    if args.cmd == "list":
        for c in load_cases():
            exp = c.expected
            logger.info(
                "Case | case_id=%s name=%s expected_decision=%s expected_severity=%s",
                c.case_id, c.name, exp.decision, exp.severity,
            )
        return

    if args.cmd == "run":
        results = asyncio.run(run_all(args.case))
        if args.json:
            logger.info(
                "Eval JSON results | payload=%s",
                json.dumps([asdict(r) for r in results], indent=2, default=str),
            )
        else:
            print_report(results)
        # Non-zero exit if anything failed — useful for CI later.
        if any(not r.passed for r in results):
            sys.exit(1)


if __name__ == "__main__":
    cli()
