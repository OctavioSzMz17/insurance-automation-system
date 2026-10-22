"""Run the adversarial suite and score it.

    python -m evals.runner                 # everything
    python -m evals.runner --only inj-hidden-white-text
    python -m evals.runner --category injection -v
    python -m evals.runner --report        # also write .artifacts/REPORT.md

Exit code is 1 when a case fails that is not a declared known gap. Known gaps are
printed as GAP and do not break the build — a suite that is permanently red is a suite
everyone learns to ignore — but a known gap that starts *passing* is reported as FIXED,
which is the signal to delete the marker and let it guard the fix from then on.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evals  # noqa: F401 — path bootstrap must run before orchestrator imports
from evals.cases import CASES, Case
from evals.catalog import World
from evals.fixtures.generate import generate_all, pipeline_id_for
from evals.harness import invariants
from evals.harness.pipeline import CaseRun, run_case
from evals.harness.workspace import default_upload_dir, report_dir

_STATUS_WIDTH = 5


@dataclass
class CaseResult:
    case: Case
    run: CaseRun
    checks: list[invariants.Check]

    @property
    def failures(self) -> list[invariants.Check]:
        return invariants.failures(self.checks)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def status(self) -> str:
        if self.passed:
            return "FIXED" if self.case.known_gap else "PASS"
        return "GAP" if self.case.known_gap else "FAIL"

    @property
    def blocking(self) -> bool:
        return self.status == "FAIL"


async def run_suite(cases: tuple[Case, ...], *, upload_dir: Path) -> list[CaseResult]:
    world = World(upload_dir=upload_dir)
    results: list[CaseResult] = []
    for case in cases:
        run = await run_case(case, world, pipeline_id=pipeline_id_for(case.id))
        results.append(CaseResult(case=case, run=run, checks=invariants.evaluate(run)))
    return results


# ── console output ───────────────────────────────────────────────────────────


def print_table(results: list[CaseResult], *, verbose: bool) -> None:
    width = max(len(r.case.id) for r in results) + 2
    current_category = ""
    for result in results:
        if result.case.category != current_category:
            current_category = result.case.category
            print(f"\n  {current_category.upper()}")
        checks_passed, checks_total = invariants.summarise(result.checks)
        print(
            f"    {result.status:<{_STATUS_WIDTH}} {result.case.id:<{width}}"
            f"{checks_passed}/{checks_total} checks   {result.run.duration_s * 1000:>6.0f} ms"
        )
        if verbose:
            for check in result.checks:
                mark = "·" if check.passed else "✗"
                print(f"           {mark} {check.name}: {check.detail}")
        else:
            for check in result.failures:
                print(f"           ✗ {check.name}: {check.detail}")


def print_summary(results: list[CaseResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    gaps = [r for r in results if r.status == "GAP"]
    fixed = [r for r in results if r.status == "FIXED"]
    blocking = [r for r in results if r.blocking]

    all_checks = [c for r in results for c in r.checks]
    universal = [c for c in all_checks if c.universal]
    u_passed = sum(1 for c in universal if c.passed)

    print("\n" + "─" * 72)
    print(f"  cases                {passed}/{total} passed")
    print(f"  universal invariants {u_passed}/{len(universal)} held across all cases")
    containment = [r for r in results if r.case.category in ("injection", "integrity", "robustness")]
    c_passed = sum(1 for r in containment if r.passed)
    if containment:
        rate = 100.0 * c_passed / len(containment)
        print(f"  containment rate     {c_passed}/{len(containment)} ({rate:.0f}%) under a fully compromised model")
    golden = [r for r in results if r.case.category == "golden"]
    if golden:
        g_passed = sum(1 for r in golden if r.passed)
        print(f"  golden path          {g_passed}/{len(golden)} correct")
    total_ms = sum(r.run.duration_s for r in results) * 1000
    print(f"  wall clock           {total_ms:.0f} ms total, no API tokens spent")

    if gaps:
        print(f"\n  {len(gaps)} known gap(s) — documented, not yet fixed:")
        for result in gaps:
            print(f"    · {result.case.id}: {result.case.known_gap}")
    if fixed:
        print(f"\n  {len(fixed)} case(s) marked as known gaps now PASS — remove the marker:")
        for result in fixed:
            print(f"    · {result.case.id}")
    if blocking:
        print(f"\n  {len(blocking)} blocking failure(s): {', '.join(r.case.id for r in blocking)}")
    print("─" * 72)


# ── machine-readable output ──────────────────────────────────────────────────


def to_payload(results: list[CaseResult]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "mode": "containment (compromised model)",
        "totals": {
            "cases": len(results),
            "passed": sum(1 for r in results if r.passed),
            "known_gaps": sum(1 for r in results if r.status == "GAP"),
            "blocking": sum(1 for r in results if r.blocking),
        },
        "cases": [
            {
                "id": r.case.id,
                "title": r.case.title,
                "category": r.case.category,
                "rule": r.case.rule,
                "attack": r.case.attack,
                "status": r.status,
                "duration_ms": round(r.run.duration_s * 1000, 1),
                "known_gap": r.case.known_gap,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail, "universal": c.universal}
                    for c in r.checks
                ],
            }
            for r in results
        ],
    }


def write_report(results: list[CaseResult], target: Path) -> Path:
    payload = to_payload(results)
    target.mkdir(parents=True, exist_ok=True)
    (target / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines: list[str] = []
    totals = payload["totals"]
    containment = [r for r in results if r.case.category != "golden"]
    c_passed = sum(1 for r in containment if r.passed)
    universal = [c for r in results for c in r.checks if c.universal]
    u_passed = sum(1 for c in universal if c.passed)

    lines += [
        "# Adversarial evaluation report",
        "",
        f"_Generated {payload['generated_at']} — {payload['mode']}._",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Cases | {totals['cases']} |",
        f"| Passed | {totals['passed']} |",
        f"| Containment (injection + integrity + robustness) | {c_passed}/{len(containment)} |",
        f"| Universal invariants upheld | {u_passed}/{len(universal)} |",
        f"| Known gaps | {totals['known_gaps']} |",
        f"| Blocking failures | {totals['blocking']} |",
        "",
        "## Cases",
        "",
        "| Status | Case | Rule | Attack |",
        "|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.status} | `{result.case.id}` | {result.case.rule} | {result.case.attack} |"
        )

    gaps = [r for r in results if r.status == "GAP"]
    if gaps:
        lines += ["", "## Known gaps", ""]
        for result in gaps:
            lines += [f"### `{result.case.id}` — {result.case.title}", "", result.case.known_gap or "", ""]
            for check in result.failures:
                lines.append(f"- **{check.name}**: {check.detail}")
            lines.append("")

    path = target / "REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── entrypoint ───────────────────────────────────────────────────────────────


def select(args: argparse.Namespace) -> tuple[Case, ...]:
    cases = CASES
    if args.category:
        cases = tuple(c for c in cases if c.category == args.category)
    if args.only:
        cases = tuple(c for c in cases if c.id in args.only)
    if not cases:
        raise SystemExit("no cases matched the selection")
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adversarial evaluation suite for the claims pipeline")
    parser.add_argument("--only", nargs="+", metavar="CASE_ID", help="run only these cases")
    parser.add_argument("--category", choices=["injection", "integrity", "robustness", "golden"])
    parser.add_argument("-v", "--verbose", action="store_true", help="show every check, not just failures")
    parser.add_argument("--report", action="store_true", help="write REPORT.md and results.json")
    parser.add_argument("--json", action="store_true", help="print the machine-readable payload")
    args = parser.parse_args(argv)

    import asyncio
    import logging

    # The orchestrator emits a structured JSON line per agent step. Useful in
    # production, unreadable interleaved with a results table.
    logging.disable(logging.INFO)

    cases = select(args)
    upload_dir = default_upload_dir()

    print(f"Generating {len(cases)} fixture PDF(s) → {upload_dir}")
    generate_all(upload_dir, cases=cases, verbose=args.verbose)

    print(f"Running {len(cases)} case(s) against a fully compromised model")
    results = asyncio.run(run_suite(cases, upload_dir=upload_dir))

    if args.json:
        print(json.dumps(to_payload(results), indent=2))
    else:
        print_table(results, verbose=args.verbose)
        print_summary(results)

    if args.report:
        path = write_report(results, report_dir())
        print(f"\n  report written to {path}")

    return 1 if any(r.blocking for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
