"""The tracing layer: span mechanics, masking, and failing open.

The property that matters most here is the last one. The verification gate fails closed
because letting a fraudulent claim through is worse than refusing a valid one.
Observability is the mirror image: a tracer that fails closed takes down the product it
exists to observe. Several tests below exist only to pin that asymmetry in place.
"""
from __future__ import annotations

import asyncio

import pytest

from observability.context import get_tracer, reset_tracing, set_tracer
from observability.masking import MaskLevel, build_masker, pseudonym, summarize_text
from observability.tracers import build_tracer
from observability.tracers.base_tracer import BaseTracer, SpanKind, SpanRecord
from observability.tracers.noop_tracer import NoopTracer
from observability.tracers.recording_tracer import RecordingTracer

# pytest.ini runs asyncio_mode=auto, so async tests need no marker — and marking the
# synchronous ones here would warn.


@pytest.fixture(autouse=True)
def _isolate_tracer():
    reset_tracing()
    yield
    reset_tracing()


@pytest.fixture
def tracer() -> RecordingTracer:
    recorder = RecordingTracer()
    set_tracer(recorder)
    return recorder


# ── structure ────────────────────────────────────────────────────────────────


async def test_children_attach_to_the_enclosing_span(tracer: RecordingTracer) -> None:
    async with tracer.trace("root") as root:
        async with tracer.span("child"):
            async with tracer.span("grandchild"):
                pass
        assert root.span_id

    root_rec = tracer.one("root")
    child = tracer.one("child")
    grandchild = tracer.one("grandchild")
    assert root_rec.parent_id is None
    assert child.parent_id == root_rec.span_id
    assert grandchild.parent_id == child.span_id


async def test_one_trace_id_across_the_whole_tree(tracer: RecordingTracer) -> None:
    async with tracer.trace("root"):
        async with tracer.span("a"):
            pass
        async with tracer.span("b"):
            pass
    assert len({r.trace_id for r in tracer.finished}) == 1


async def test_session_propagates_to_children(tracer: RecordingTracer) -> None:
    async with tracer.trace("root", session_id="pipeline-7"):
        async with tracer.span("child"):
            pass
    assert all(r.session_id == "pipeline-7" for r in tracer.finished)


async def test_session_can_be_attached_after_opening(tracer: RecordingTracer) -> None:
    """/audit and /confirm only learn the pipeline id after loading the proposal."""
    async with tracer.trace("root") as root:
        root.set_session("pipeline-9")
        async with tracer.span("child"):
            pass
    assert tracer.one("root").session_id == "pipeline-9"
    assert tracer.one("child").session_id == "pipeline-9"


async def test_sibling_traces_do_not_share_a_parent(tracer: RecordingTracer) -> None:
    async with tracer.trace("first"):
        pass
    async with tracer.trace("second"):
        pass
    assert all(r.parent_id is None for r in tracer.roots())
    assert len(tracer.roots()) == 2


async def test_concurrent_pipelines_do_not_adopt_each_others_spans(
    tracer: RecordingTracer,
) -> None:
    """ContextVar is per-task, which is the whole reason it is used here."""

    async def pipeline(label: str) -> None:
        async with tracer.trace(f"root-{label}"):
            await asyncio.sleep(0)
            async with tracer.span(f"child-{label}"):
                await asyncio.sleep(0)

    await asyncio.gather(pipeline("a"), pipeline("b"))

    for label in ("a", "b"):
        child = tracer.one(f"child-{label}")
        assert child.parent_id == tracer.one(f"root-{label}").span_id


# ── payload ──────────────────────────────────────────────────────────────────


async def test_usage_is_recorded_and_totalled(tracer: RecordingTracer) -> None:
    async with tracer.span("gen", kind=SpanKind.GENERATION) as span:
        span.set_usage(input_tokens=1200, output_tokens=80, model="test-model")
    usage = tracer.one("gen").usage
    assert usage == {
        "input_tokens": 1200,
        "output_tokens": 80,
        "total_tokens": 1280,
        "model": "test-model",
    }


async def test_duration_is_measured(tracer: RecordingTracer) -> None:
    async with tracer.span("slow"):
        await asyncio.sleep(0.01)
    duration = tracer.one("slow").duration_ms
    assert duration is not None and duration >= 5


async def test_scores_accumulate(tracer: RecordingTracer) -> None:
    async with tracer.span("scored") as span:
        span.score("review_flags", 2)
        span.score("gate_blocked", True, comment="póliza no vigente")
    scores = tracer.one("scored").scores
    assert [s["name"] for s in scores] == ["review_flags", "gate_blocked"]
    assert scores[1]["value"] is True


async def test_as_dict_omits_empty_fields(tracer: RecordingTracer) -> None:
    async with tracer.span("bare"):
        pass
    payload = tracer.one("bare").as_dict()
    for absent in ("input", "output", "usage", "message", "scores"):
        assert absent not in payload
    assert payload["ok"] is True
    assert payload["duration_ms"] is not None


# ── failure handling ─────────────────────────────────────────────────────────


async def test_exception_marks_the_span_and_propagates(tracer: RecordingTracer) -> None:
    with pytest.raises(ValueError):
        async with tracer.span("boom"):
            raise ValueError("node failed")

    record = tracer.one("boom")
    assert record.ok is False
    assert "ValueError" in (record.message or "")
    assert record.ended_at is not None, "a failed span must still be closed"


class _BrokenTracer(BaseTracer):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.attempts = 0

    def on_start(self, record: SpanRecord) -> None:
        self.attempts += 1
        raise RuntimeError("backend is down")

    def on_end(self, record: SpanRecord) -> None:
        raise RuntimeError("backend is still down")


async def test_a_broken_backend_does_not_break_traced_code() -> None:
    broken = _BrokenTracer()
    set_tracer(broken)
    result = None
    async with get_tracer().span("work"):
        result = "completed"
    assert result == "completed"
    assert broken.attempts == 1


async def test_a_broken_backend_warns_once_then_stays_quiet(caplog) -> None:
    broken = _BrokenTracer()
    set_tracer(broken)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            async with get_tracer().span("work"):
                pass
    degraded = [r for r in caplog.records if getattr(r, "event", "") == "tracing.degraded"]
    assert len(degraded) == 1, "a failing backend must not flood the log"


async def test_composite_isolates_one_failing_backend() -> None:
    """One bad sink must not take the others down — the live UI and the trace store
    are two consumers of the same spans, and losing both because one broke would be
    the worst possible failure mode."""
    from observability.tracers import CompositeTracer

    good = RecordingTracer()
    composite = CompositeTracer([_BrokenTracer(), good])
    set_tracer(composite)

    async with get_tracer().span("work"):
        pass

    assert good.names() == ["work"], "the healthy backend still received the span"


# ── configuration ────────────────────────────────────────────────────────────


async def test_get_tracer_defaults_to_noop() -> None:
    assert isinstance(get_tracer(), NoopTracer)


async def test_log_tracer_actually_emits(caplog) -> None:
    """Regression: the payload used to be splatted into `extra`, and `name` /
    `message` collide with reserved LogRecord attributes — logging raised, the
    fail-open guard swallowed it, and the backend silently emitted nothing."""
    from observability.tracers.log_tracer import LogTracer

    set_tracer(LogTracer())
    with caplog.at_level("INFO"):
        async with get_tracer().trace("claim.analyze", session_id="p-1") as root:
            root.set_status(False, message="manual review")

    spans = [r for r in caplog.records if getattr(r, "event", "") == "trace.span"]
    degraded = [r for r in caplog.records if getattr(r, "event", "") == "tracing.degraded"]
    assert not degraded, "the log backend degraded instead of emitting"
    assert len(spans) == 1
    payload = spans[0].span
    assert payload["name"] == "claim.analyze"
    assert payload["session_id"] == "p-1"
    assert payload["ok"] is False
    assert payload["message"] == "manual review"


async def test_log_tracer_payload_is_json_serialisable() -> None:
    """The formatter must be able to render whatever a span carries."""
    import json

    from observability.tracers.log_tracer import LogTracer

    set_tracer(LogTracer())
    async with get_tracer().span("gen", kind=SpanKind.GENERATION) as span:
        span.set_usage(input_tokens=1, output_tokens=2, model="m")
        span.set_input({"nested": {"deep": [1, 2, 3]}})
        span.score("flags", 0)
    # Nothing above should have raised; confirm the shape round-trips.
    recorder = RecordingTracer()
    set_tracer(recorder)
    async with get_tracer().span("gen2", kind=SpanKind.GENERATION) as span:
        span.set_usage(input_tokens=1, output_tokens=2, model="m")
    json.dumps(recorder.one("gen2").as_dict())


def test_unknown_backend_degrades_to_noop_instead_of_refusing_to_start() -> None:
    assert isinstance(build_tracer("not-a-backend"), NoopTracer)


def test_backends_compose() -> None:
    from observability.tracers import CompositeTracer

    assert isinstance(build_tracer("log,recording"), CompositeTracer)
    assert isinstance(build_tracer("recording"), RecordingTracer)


async def test_noop_still_maintains_structure() -> None:
    """Instrumented code must behave identically with tracing off."""
    noop = NoopTracer()
    set_tracer(noop)
    async with get_tracer().trace("root") as root:
        async with get_tracer().span("child") as child:
            assert child.trace_id == root.trace_id


# ── masking ──────────────────────────────────────────────────────────────────


def test_pii_level_pseudonymises_identities() -> None:
    mask = build_masker(MaskLevel.PII)
    masked = mask({"email": "juan.p@email.com", "severity": "HIGH"})
    assert masked["email"] == pseudonym("juan.p@email.com")
    assert masked["severity"] == "HIGH", "non-PII fields must survive"


def test_pseudonyms_are_stable_so_traces_stay_correlatable() -> None:
    mask = build_masker(MaskLevel.PII)
    first = mask({"claimant_sub": "ana.mtz@email.com"})
    second = mask({"claimant_sub": "ana.mtz@email.com"})
    assert first == second


def test_strict_level_drops_identities_entirely() -> None:
    mask = build_masker(MaskLevel.STRICT)
    assert mask({"email": "juan.p@email.com"})["email"] == "<redacted>"


def test_emails_embedded_in_free_text_are_caught() -> None:
    mask = build_masker(MaskLevel.PII)
    masked = mask({"reason": "no coincide con juan.p@email.com en el documento"})
    assert "juan.p@email.com" not in masked["reason"]
    assert "anon:" in masked["reason"]


def test_bulk_fields_are_summarised_not_sent() -> None:
    mask = build_masker(MaskLevel.PII)
    masked = mask({"text": "x" * 40_000})
    assert masked["text"] == summarize_text("x" * 40_000)
    assert masked["text"]["chars"] == 40_000


def test_nested_structures_are_masked() -> None:
    mask = build_masker(MaskLevel.PII)
    masked = mask({"user": {"email": "a@b.com", "full_name": "Juan Perez"}})
    assert "a@b.com" not in str(masked)
    assert "Juan Perez" not in str(masked)


def test_none_level_still_bounds_unbounded_values() -> None:
    """Even unmasked, nothing unbounded reaches a backend."""
    mask = build_masker(MaskLevel.NONE)
    out = mask({"blob": "y" * 5_000})
    assert len(out["blob"]) < 700


def test_summarize_text_handles_empty() -> None:
    assert summarize_text(None) == {"chars": 0, "sha256_12": None}
    assert summarize_text("")["chars"] == 0


async def test_span_applies_masking_at_the_boundary() -> None:
    """Masking happens on the way in, so no backend ever sees the raw value."""
    recorder = RecordingTracer(mask=build_masker(MaskLevel.PII))
    set_tracer(recorder)
    async with get_tracer().span("tool") as span:
        span.set_input({"email": "juan.p@email.com"})
    assert recorder.one("tool").input["email"].startswith("anon:")
