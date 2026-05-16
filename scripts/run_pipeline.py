"""End-to-end CLI runner using the AgentRouter (B3.2).

Before B3.2 this script orchestrated the analyst → auditor flow itself
(~150 lines). Now it just calls one method on AgentRouter and prints
the structured result.

Usage:
    py scripts/run_pipeline.py                       # default sample PDF
    py scripts/run_pipeline.py <path/to/claim.pdf>   # custom PDF
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "ai-orchestrator"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from routes.agent_router import AgentRouter  # noqa: E402


DEFAULT_PDF = r"C:\Users\octav\OneDrive\Escritorio\Real-World Project\Claim_Report_JP01.pdf"


async def main(pdf_path: str) -> int:
    print("=" * 78)
    print("  PHASE B — Validated Pipeline via AgentRouter (B3.2)")
    print("=" * 78)
    print(f"PDF: {pdf_path}\n")

    router = AgentRouter()
    result = await router.process_claim(pdf_path)

    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False, default=str))
    print()
    print("=" * 78)
    decision = (result.claim_decision or {}).get("decision", "—")
    persisted = (result.claim_decision or {}).get("persisted_claim_id", "—")
    print(
        f"  status: {result.pipeline_status}   |   decision: {decision}   "
        f"|   persisted_claim_id: {persisted}"
    )
    print(
        f"  retries: analyst={result.analyst_retries} auditor={result.auditor_retries}   "
        f"|   latency: {result.total_latency_ms} ms"
    )
    print("=" * 78)

    return 0 if result.pipeline_status == "SUCCESS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run validated pipeline via AgentRouter")
    parser.add_argument("pdf_path", nargs="?", default=DEFAULT_PDF, help=f"PDF (default: {DEFAULT_PDF})")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.pdf_path)))
