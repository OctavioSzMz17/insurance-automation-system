"""End-to-end CLI runner — analyst → auditor pipeline.

Usage:
    py scripts/run_auditor.py                       # uses default sample PDF
    py scripts/run_auditor.py <path/to/claim.pdf>   # custom PDF

Flow:
    1. AnalystAgent parses the PDF and produces a TriageReport
    2. AuditorAgent reads the TriageReport, applies business rules,
       calls create_claim to persist the decision, and returns the
       final ClaimDecision dict.

Reads LLM_PROVIDER / LLM_PROVIDER_API_KEY / MODEL_NAME from .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "ai-orchestrator" / "agents"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyst_agent import AnalystAgent  # noqa: E402
from auditor_agent import AuditorAgent  # noqa: E402


DEFAULT_PDF = r"C:\Users\octav\OneDrive\Escritorio\Real-World Project\Claim_Report_JP01.pdf"


def _short(text: str, n: int = 120) -> str:
    return str(text)[:n].replace("\n", " ")


def _print_step(step: dict) -> None:
    s = step["step"]
    if s == "tool_call":
        print(f'    [tool_call]   {step["tool"]}({_short(step["arguments"], 80)})')
    elif s == "tool_result":
        print(f'    [tool_result] {step.get("tool")} -> {_short(step.get("content", ""))}...')
    elif s == "ai_message":
        print(f'    [ai_message]  {_short(step["content"])}...')
    elif s == "error":
        print(f'    [ERROR]       {step.get("error")}')
    else:
        print(f'    [{s}]')


async def main(pdf_path: str) -> int:
    print("=" * 75)
    print("  PHASE B PIPELINE — Analyst → Auditor")
    print("=" * 75)
    print(f"PDF: {pdf_path}\n")

    # ── Stage 1: Analyst ──────────────────────────────────────────
    print(">> Stage 1: Analyst agent (extraction + triage)")
    analyst = AnalystAgent()
    a_state = await analyst.run({"pdf_path": pdf_path})

    print(f"   trail steps: {len(a_state.reasoning_trail)}")
    for step in a_state.reasoning_trail:
        _print_step(step)
    print()

    if a_state.last_error or not a_state.result:
        print(f"!! Analyst failed: {a_state.last_error or 'no result'}")
        return 1

    print("--- TriageReport ---")
    print(json.dumps(a_state.result, indent=2, ensure_ascii=False))
    print()

    # ── Stage 2: Auditor ──────────────────────────────────────────
    print(">> Stage 2: Auditor agent (business rules + final decision + DB write)")
    auditor = AuditorAgent()
    u_state = await auditor.run({"triage_report": a_state.result})

    print(f"   trail steps: {len(u_state.reasoning_trail)}")
    for step in u_state.reasoning_trail:
        _print_step(step)
    print()

    if u_state.last_error or not u_state.result:
        print(f"!! Auditor failed: {u_state.last_error or 'no result'}")
        return 1

    print("--- ClaimDecision ---")
    print(json.dumps(u_state.result, indent=2, ensure_ascii=False))
    print()
    print("=" * 75)
    decision = u_state.result.get("decision", "UNKNOWN")
    persisted = u_state.result.get("persisted_claim_id", "—")
    print(f"  FINAL DECISION: {decision}   |   persisted_claim_id: {persisted}")
    print("=" * 75)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run analyst → auditor pipeline against a claim PDF")
    parser.add_argument("pdf_path", nargs="?", default=DEFAULT_PDF, help=f"Path to a claim PDF (default: {DEFAULT_PDF})")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.pdf_path)))
