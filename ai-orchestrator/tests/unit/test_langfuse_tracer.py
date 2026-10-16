"""The Langfuse backend, verified without a Langfuse server.

Langfuse 4.x is OpenTelemetry-based, and its client accepts a `span_exporter`. Swapping
in an in-memory exporter turns an integration that would otherwise need a running stack
into an ordinary unit test: drive the tracer, then assert on the OTel spans the SDK
*would have shipped*.

That matters beyond convenience. Integration code written from documentation and never
executed is a guess, and this backend had to bridge two different context models — our
`on_start`/`on_end` hooks against the SDK's context-manager API. These tests are what
make the bridge a fact rather than a plausible-looking file.

They also pin the SDK contract: if a future Langfuse release renames `start_observation`
or changes `usage_details`, these fail instead of the backend silently shipping nothing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langfuse", reason="the langfuse backend is optional")

from langfuse import Langfuse  # noqa: E402
from langfuse import LangfuseOtelSpanAttributes as Attr  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from observability.context import reset_tracing, set_tracer  # noqa: E402
from observability.masking import MaskLevel, build_masker  # noqa: E402
from observability.tracers.base_tracer import SpanKind  # noqa: E402
from observability.tracers.langfuse_tracer import (  # noqa: E402
    LangfuseTracer,
    _data_type,
)


@pytest.fixture(scope="module")
def _sdk() -> tuple[Langfuse, InMemorySpanExporter]:
    """One client for the module, because `Langfuse` is a singleton per public_key.

    Constructing a second instance with the same key returns the first one and
    *silently ignores the new arguments* — including `span_exporter`. A per-test client
    therefore kept exporting into the first test's exporter, which looked like the
    backend dropping spans. One client, cleared between tests.
    """
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        # Never contacted for spans: the exporter is replaced. Scores do go over HTTP,
        # which is why the score test asserts isolation rather than delivery.
        base_url="http://127.0.0.1:1",
        span_exporter=exporter,
        tracing_enabled=True,
    )
    return client, exporter


@pytest.fixture
def exporter(_sdk: tuple[Langfuse, InMemorySpanExporter]) -> InMemorySpanExporter:
    _, exporter = _sdk
    exporter.clear()
    return exporter


@pytest.fixture
def tracer(_sdk: tuple[Langfuse, InMemorySpanExporter]) -> LangfuseTracer:
    client, _ = _sdk
    instance = LangfuseTracer(client=client, mask=build_masker(MaskLevel.PII))
    set_tracer(instance)
    yield instance
    reset_tracing()


def _spans(exporter: InMemorySpanExporter, client_tracer: LangfuseTracer) -> dict:
    client_tracer._client.flush()  # noqa: SLF001 — test introspection
    return {s.name: s for s in exporter.get_finished_spans()}


# ── structure ────────────────────────────────────────────────────────────────


async def test_span_tree_is_shipped_with_correct_parentage(tracer, exporter) -> None:
    async with tracer.trace("claim.analyze", session_id="pipeline-42") as root:
        assert root.span_id
        async with tracer.span("analyst.run"):
            async with tracer.span("analyst.think_act", kind=SpanKind.GENERATION):
                pass

    spans = _spans(exporter, tracer)
    assert set(spans) == {"claim.analyze", "analyst.run", "analyst.think_act"}

    by_id = {s.context.span_id: s.name for s in spans.values()}
    assert spans["claim.analyze"].parent is None
    assert by_id[spans["analyst.run"].parent.span_id] == "claim.analyze"
    assert by_id[spans["analyst.think_act"].parent.span_id] == "analyst.run"


async def test_one_otel_trace_for_the_whole_tree(tracer, exporter) -> None:
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("child"):
            pass
    spans = _spans(exporter, tracer)
    assert len({s.context.trace_id for s in spans.values()}) == 1


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (SpanKind.INTERNAL, "span"),
        (SpanKind.GENERATION, "generation"),
        (SpanKind.TOOL, "tool"),
        (SpanKind.EVENT, "event"),
    ],
)
async def test_every_span_kind_maps_to_a_native_observation_type(
    tracer, exporter, kind, expected
) -> None:
    """Langfuse has native tool and event types, so nothing is flattened."""
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("probe", kind=kind):
            pass
    spans = _spans(exporter, tracer)
    assert spans["probe"].attributes[Attr.OBSERVATION_TYPE] == expected


# ── payload ──────────────────────────────────────────────────────────────────


async def test_session_propagates_to_the_whole_subtree(tracer, exporter) -> None:
    """One claim is one session even though /analyze and /confirm are separate requests."""
    async with tracer.trace("claim.analyze", session_id="pipeline-42"):
        async with tracer.span("analyst.run"):
            pass
    spans = _spans(exporter, tracer)
    for span in spans.values():
        assert span.attributes[Attr.TRACE_SESSION_ID] == "pipeline-42"


async def test_usage_becomes_usage_details_and_model(tracer, exporter) -> None:
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("gen", kind=SpanKind.GENERATION) as span:
            span.set_usage(input_tokens=1204, output_tokens=86, model="claude-sonnet-4-6")
    spans = _spans(exporter, tracer)
    gen = spans["gen"]
    assert gen.attributes[Attr.OBSERVATION_MODEL] == "claude-sonnet-4-6"
    usage = gen.attributes[Attr.OBSERVATION_USAGE_DETAILS]
    assert "1204" in usage and "86" in usage
    assert "total_tokens" not in usage, "Langfuse derives the total; sending it is noise"


async def test_attributes_become_metadata(tracer, exporter) -> None:
    """Span attributes land as `langfuse.observation.metadata.<field>`, one key each,
    which is what makes them filterable in the UI rather than an opaque blob."""
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("validate", severity_computed="LOW") as span:
            span.set_attributes(severity_claimed="CRITICAL", severity_overridden=True)
    attributes = _spans(exporter, tracer)["validate"].attributes
    assert attributes[f"{Attr.OBSERVATION_METADATA}.severity_claimed"] == "CRITICAL"
    assert attributes[f"{Attr.OBSERVATION_METADATA}.severity_computed"] == "LOW"
    assert attributes[f"{Attr.OBSERVATION_METADATA}.severity_overridden"] is True


async def test_failure_sets_error_level_and_message(tracer, exporter) -> None:
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("gate") as span:
            span.set_status(False, message="póliza no vigente")
    spans = _spans(exporter, tracer)
    gate = spans["gate"]
    assert gate.attributes[Attr.OBSERVATION_LEVEL] == "ERROR"
    assert "vigente" in gate.attributes[Attr.OBSERVATION_STATUS_MESSAGE]


async def test_an_exception_is_shipped_as_an_error(tracer, exporter) -> None:
    with pytest.raises(ValueError):
        async with tracer.trace("root", session_id="s"):
            async with tracer.span("boom"):
                raise ValueError("node failed")
    spans = _spans(exporter, tracer)
    assert spans["boom"].attributes[Attr.OBSERVATION_LEVEL] == "ERROR"


async def test_masking_is_applied_before_the_sdk_sees_anything(tracer, exporter) -> None:
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("tool", kind=SpanKind.TOOL) as span:
            span.set_input({"email": "juan.p@email.com"})
    spans = _spans(exporter, tracer)
    payload = str(spans["tool"].attributes)
    assert "juan.p@email.com" not in payload
    assert "anon:" in payload


# ── robustness ───────────────────────────────────────────────────────────────


async def test_a_failing_score_does_not_cost_us_the_span(tracer, exporter) -> None:
    """Scores travel over HTTP, which is unreachable here — the span must still ship."""
    async with tracer.trace("root", session_id="s") as root:
        root.score("gate_blocked", True)
    spans = _spans(exporter, tracer)
    assert "root" in spans, "the span was lost because a score failed"


async def test_no_leftover_state_after_a_tree_closes(tracer, exporter) -> None:
    """The span_id → observation map must drain, or a long-lived process leaks."""
    async with tracer.trace("root", session_id="s"):
        async with tracer.span("child"):
            pass
    assert tracer._observations == {}  # noqa: SLF001 — leak check


async def test_shutdown_flushes(tracer) -> None:
    async with tracer.trace("root", session_id="s"):
        pass
    tracer.shutdown()  # must not raise


def test_missing_credentials_refuse_to_build_rather_than_pretend() -> None:
    """A service must not boot believing it is traced when it is not."""
    import os

    saved = {k: os.environ.pop(k, None) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")}
    try:
        with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
            LangfuseTracer()
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_the_factory_degrades_instead_of_failing_boot(caplog) -> None:
    import os

    from observability.tracers import build_tracer
    from observability.tracers.noop_tracer import NoopTracer

    saved = {k: os.environ.pop(k, None) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")}
    try:
        with caplog.at_level("WARNING"):
            built = build_tracer("langfuse")
        assert isinstance(built, NoopTracer)
        assert any(getattr(r, "event", "") == "tracing.backend_failed" for r in caplog.records)
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, "BOOLEAN"), (False, "BOOLEAN"), (1, "NUMERIC"), (0.5, "NUMERIC"),
     ("approve", "CATEGORICAL")],
)
def test_score_data_types(value, expected) -> None:
    """Langfuse infers a bare 1 as NUMERIC, so BOOLEAN has to be stated."""
    assert _data_type(value) == expected
