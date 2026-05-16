"""CLI runner — manually trigger the analyst_agent against a claim PDF.

Usage:
    py scripts/run_analyst.py                       # uses default sample PDF
    py scripts/run_analyst.py <path/to/claim.pdf>   # custom PDF

Reads LLM_PROVIDER / LLM_PROVIDER_API_KEY / MODEL_NAME from .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Make ai-orchestrator/agents importable
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "ai-orchestrator" / "agents"))

# UTF-8 for Spanish characters in console
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyst_agent import AnalystAgent  # noqa: E402


DEFAULT_PDF = r"C:\Users\octav\OneDrive\Escritorio\Real-World Project\Claim_Report_JP01.pdf"


def _print_step(step: dict) -> None:
    """Compact, human-friendly printout of one reasoning trail entry."""
    s = step["step"]
    if s == "tool_call":
        print(f'  [tool_call]   {step["tool"]}({step["arguments"]})')
    elif s == "tool_result":
        preview = str(step.get("content", ""))[:120].replace("\n", " ")
        print(f'  [tool_result] {step.get("tool")} -> {preview}...')
    elif s == "ai_message":
        preview = step["content"][:120].replace("\n", " ")
        print(f'  [ai_message]  {preview}...')
    elif s == "error":
        print(f'  [ERROR]       {step.get("error")}')
    else:
        print(f'  [{s}]')


async def main(pdf_path: str) -> int:
    print("=" * 70)
    print("  ANALYST AGENT — Insurance Claim Triage (B2.2)")
    print("=" * 70)
    print(f"PDF: {pdf_path}")
    print()

    agent = AnalystAgent()
    state = await agent.run({"pdf_path": pdf_path})

    print("--- Reasoning trail ---")
    for step in state.reasoning_trail:
        _print_step(step)
    print()

    if state.last_error:
        print(f"FAILED: {state.last_error}")
        return 1

    print("--- Final triage report ---")
    print(json.dumps(state.result, indent=2, ensure_ascii=False))
    print()
    print("=" * 70)
    print(f"  Status: {state.result.get('status') if state.result else 'UNKNOWN'} "
          f"| Flags: {len(state.result.get('flags', [])) if state.result else 0}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run analyst_agent against a claim PDF")
    parser.add_argument(
        "pdf_path",
        nargs="?",
        default=DEFAULT_PDF,
        help=f"Path to a claim PDF (default: {DEFAULT_PDF})",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.pdf_path)))
