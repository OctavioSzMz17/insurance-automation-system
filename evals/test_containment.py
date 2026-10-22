"""Expose the adversarial suite to pytest so CI runs it alongside the unit tests.

Two entry points on purpose:

* `python -m evals.runner` is the human-facing view — a scored table, metrics and a
  Markdown report.
* this module is the machine-facing view — one test per case, so a failure shows up in
  the same place as every other regression and blocks the same pipeline.

Cases carrying a `known_gap` are reported as xfail: documented, tracked, and *strict*, so
the moment the underlying gap is fixed the xpass forces the marker to be removed and the
case becomes a permanent guard.
"""
from __future__ import annotations

import pytest

import evals  # noqa: F401 — path bootstrap must run before orchestrator imports
from evals.cases import CASES, Case
from evals.catalog import World
from evals.fixtures.generate import generate_all, pipeline_id_for
from evals.harness import invariants
from evals.harness.pipeline import run_case

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    upload_dir = tmp_path_factory.mktemp("evals-uploads")
    generate_all(upload_dir)
    return World(upload_dir=upload_dir)


def _ids(cases: tuple[Case, ...]) -> list[str]:
    return [c.id for c in cases]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_case(case: Case, world: World) -> None:
    if case.known_gap:
        pytest.xfail(f"known gap: {case.known_gap}")

    run = await run_case(case, world, pipeline_id=pipeline_id_for(case.id))
    checks = invariants.evaluate(run)
    failed = invariants.failures(checks)
    assert not failed, "\n".join(f"{c.name}: {c.detail}" for c in failed)


async def test_universal_invariants_cover_every_case(world: World) -> None:
    """Guard the guard: every case must contribute the full set of universal checks.

    If a universal invariant silently stops being evaluated — because a run crashed
    early, say — the suite would get quieter rather than louder. This asserts the
    expected number of universal checks actually ran.
    """
    expected_per_case = 8
    for case in CASES:
        run = await run_case(case, world, pipeline_id=pipeline_id_for(case.id))
        universal = [c for c in invariants.evaluate(run) if c.universal]
        assert len(universal) == expected_per_case, (
            f"{case.id}: {len(universal)} universal checks evaluated, "
            f"expected {expected_per_case}"
        )
