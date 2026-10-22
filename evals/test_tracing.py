"""Pin the shape of a real claim's trace.

Instrumentation rots quietly: someone refactors a graph node, the `async with` goes with
it, and no test notices because no test looked at the spans. These do look — against the
same full pipeline the containment suite drives, so what is asserted here is the trace a
real claim actually produces, not a trace assembled by the test.
"""
from __future__ import annotations

import pytest

import evals  # noqa: F401 — path bootstrap must run before orchestrator imports
from evals.cases import by_id
from evals.catalog import World
from evals.fixtures.generate import generate_all, pipeline_id_for
from evals.harness.pipeline import run_case
from observability.context import reset_tracing, set_tracer
from observability.masking import MaskLevel, build_masker
from observability.tracers.base_tracer import SpanKind
from observability.tracers.recording_tracer import RecordingTracer


async def _trace_case(case_id: str, world: World) -> RecordingTracer:
    case = by_id(case_id)
    tracer = RecordingTracer(mask=build_masker(MaskLevel.PII))
    set_tracer(tracer)
    try:
        await run_case(case, world, pipeline_id=pipeline_id_for(case.id))
    finally:
        reset_tracing()
    return tracer


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    upload_dir = tmp_path_factory.mktemp("trace-uploads")
    generate_all(upload_dir)
    return World(upload_dir=upload_dir)


async def test_a_claim_produces_the_expected_span_tree(world: World) -> None:
    tracer = await _trace_case("gold-medium-boundary", world)
    names = set(tracer.names())

    for expected in (
        "claim.analyze", "analyst.run", "analyst.think_act", "analyst.validate",
        "verification", "check.coverage", "check.ownership", "check.identity",
        "check.amount", "auditor.run", "auditor.think_act",
        "claim.confirm", "mutation.gate",
    ):
        assert expected in names, f"missing {expected!r} span\n{tracer.tree()}"


async def test_tool_calls_each_get_their_own_span(world: World) -> None:
    tracer = await _trace_case("gold-medium-boundary", world)
    tool_spans = [r for r in tracer.finished if r.kind is SpanKind.TOOL]
    assert tool_spans, tracer.tree()
    assert {r.name for r in tool_spans} >= {
        "tool.documents.parse_pdf",
        "tool.users.get_user_by_email",
        "tool.policies.get_policies_by_user_id",
    }


async def test_the_whole_claim_shares_one_session(world: World) -> None:
    """/analyze and /confirm are separate requests; the session is what joins them."""
    tracer = await _trace_case("gold-medium-boundary", world)
    pipeline_id = str(pipeline_id_for("gold-medium-boundary"))
    sessions = {r.session_id for r in tracer.finished}
    assert sessions == {pipeline_id}, f"spans split across sessions: {sessions}"
    assert len(tracer.roots()) >= 2, "analyze and confirm are distinct traces"


async def test_generation_spans_carry_usage_and_prompt_version(world: World) -> None:
    tracer = await _trace_case("gold-medium-boundary", world)
    generations = [r for r in tracer.finished if r.kind is SpanKind.GENERATION]
    assert generations
    for record in generations:
        assert record.usage is not None, f"{record.name} has no usage"
        assert record.usage["total_tokens"] >= 0
        assert record.attributes.get("prompt_version"), (
            f"{record.name} did not record which prompt text produced it"
        )


async def test_severity_override_is_visible_in_the_trace(world: World) -> None:
    """The containment, made legible: the model asked for CRITICAL, the server stored LOW."""
    tracer = await _trace_case("inj-severity-escalation", world)
    validate = tracer.one("analyst.validate")
    assert validate.attributes["severity_claimed"] == "CRITICAL"
    assert validate.attributes["severity_computed"] == "LOW"
    assert validate.attributes["severity_overridden"] is True


async def test_a_blocked_approval_is_visible_in_the_trace(world: World) -> None:
    tracer = await _trace_case("inj-ignore-instructions", world)
    gate = tracer.one("mutation.gate")
    assert gate.attributes["blocked"] is True
    assert gate.attributes["flags"] >= 1
    assert gate.ok is False
    assert {"name": "gate_blocked", "value": True} in gate.scores


async def test_failed_checks_mark_their_span(world: World) -> None:
    tracer = await _trace_case("int-cross-tenant-policy", world)
    assert tracer.one("check.ownership").ok is False
    assert tracer.one("check.coverage").ok is True
    assert tracer.one("verification").ok is False


async def test_tool_spans_record_the_forced_identity(world: World) -> None:
    """The security invariant, visible: the caller on every tool call is the server's."""
    tracer = await _trace_case("int-caller-spoofing", world)
    tool_spans = [r for r in tracer.finished if r.kind is SpanKind.TOOL]
    assert tool_spans
    for record in tool_spans:
        assert record.attributes["identity_forced"] is True
        assert record.attributes["caller_sub"].startswith("anon:"), (
            "identities must be pseudonymised before leaving the process"
        )


async def test_no_document_text_reaches_a_span(world: World) -> None:
    """The hard privacy rule: a PDF body must never travel in a trace."""
    tracer = await _trace_case("gold-medium-boundary", world)
    needle = "ASEGURADORA LIA"
    for record in tracer.finished:
        blob = str(record.as_dict())
        assert needle not in blob, f"{record.name} leaked document text"


async def test_a_manual_review_run_marks_its_spans(world: World) -> None:
    tracer = await _trace_case("rob-malformed-pdf", world)
    assert tracer.one("analyst.run").ok is False
    assert tracer.one("claim.analyze").ok is False
    assert "analyst.escalate" in tracer.names()
