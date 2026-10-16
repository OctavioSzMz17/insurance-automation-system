"""Tracer backends and the factory that selects one.

Adding a backend means adding a file here and a row in `_BACKENDS` — never touching a
call site (Rule 9). `OBSERVABILITY_BACKEND` picks it; several names separated by commas
compose them, which is how the same spans will reach both a trace store and the live UI.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from observability.masking import MaskLevel, build_masker
from observability.tracers.base_tracer import (
    BaseTracer,
    Span,
    SpanKind,
    SpanRecord,
    Tracer,
)
from observability.tracers.log_tracer import LogTracer
from observability.tracers.noop_tracer import NoopTracer
from observability.tracers.recording_tracer import RecordingTracer


def _langfuse_factory(**kwargs: Any) -> BaseTracer:
    """Imported lazily so `langfuse` is only required when it is actually selected."""
    from observability.tracers.langfuse_tracer import LangfuseTracer

    return LangfuseTracer(**kwargs)


_BACKENDS: dict[str, Callable[..., BaseTracer]] = {
    "noop": NoopTracer,
    "log": LogTracer,
    "recording": RecordingTracer,
    "langfuse": _langfuse_factory,
    # "eventbus": EventBusTracer,   # phase 4 — feeds the live graph UI
}


class CompositeTracer(BaseTracer):
    """Fans every span out to several backends.

    The point of composing rather than choosing: the durable trace store and the live UI
    are two consumers of one instrumentation, not two instrumentations. A backend that
    breaks is isolated by `BaseTracer._safely`, so one bad sink cannot take the others
    down with it.
    """

    def __init__(self, backends: list[BaseTracer], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._backends = backends

    def on_start(self, record: SpanRecord) -> None:
        for backend in self._backends:
            backend._safely("on_start", record)  # noqa: SLF001 — same-family call

    def on_end(self, record: SpanRecord) -> None:
        for backend in self._backends:
            backend._safely("on_end", record)  # noqa: SLF001 — same-family call

    def shutdown(self) -> None:
        for backend in self._backends:
            backend._safely("shutdown")  # noqa: SLF001 — same-family call


def build_tracer(
    backend: str | None = None,
    *,
    environment: str | None = None,
    mask_level: str | None = None,
    **backend_kwargs: Any,
) -> Tracer:
    """Build the tracer named by `backend`, or by `OBSERVABILITY_BACKEND`.

    An unknown name degrades to a no-op tracer with a warning rather than refusing to
    start: a typo in an observability setting must not keep the service down.
    """
    from observability.logging import get_logger

    logger = get_logger(__name__)
    raw = (backend or os.environ.get("OBSERVABILITY_BACKEND", "noop")).strip().lower()
    level = MaskLevel(mask_level) if mask_level else None
    masker = build_masker(level) if level else build_masker()
    if level is None and environment is not None:
        from observability.masking import default_level

        masker = build_masker(default_level(environment))

    names = [name.strip() for name in raw.split(",") if name.strip()] or ["noop"]
    built: list[BaseTracer] = []
    for name in names:
        factory = _BACKENDS.get(name)
        if factory is None:
            logger.warning(
                "unknown tracing backend; ignoring",
                extra={"event": "tracing.unknown_backend", "backend": name,
                       "known": sorted(_BACKENDS)},
            )
            continue
        try:
            built.append(factory(mask=masker, **backend_kwargs))
        except Exception as exc:  # noqa: BLE001 — a misconfigured sink must not block boot
            logger.warning(
                "tracing backend could not be built; ignoring",
                extra={"event": "tracing.backend_failed", "backend": name, "err": str(exc)},
            )

    if not built:
        return NoopTracer(mask=masker)
    if len(built) == 1:
        return built[0]
    return CompositeTracer(built, mask=masker)


__all__ = [
    "BaseTracer",
    "CompositeTracer",
    "LogTracer",
    "NoopTracer",
    "RecordingTracer",
    "Span",
    "SpanKind",
    "SpanRecord",
    "Tracer",
    "build_tracer",
]
