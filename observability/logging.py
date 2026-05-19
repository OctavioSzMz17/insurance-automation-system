"""Logging bootstrap — Phase D3.1.

Single entry point (`configure_logging`) that:
    1. Sets the root log level from settings.
    2. Picks a formatter (human text or JSON) per `log_format`.
    3. Attaches the trace_id filter from D2.4 so every record carries
       the active request's id without per-call boilerplate.

Call this once at FastAPI app startup, BEFORE the first log line we
care about. Idempotent — calling twice is safe but pointless.

Why this module owns logging instead of `app.py`:
    - `app.py` is FastAPI-specific. Background scripts, alembic
      migrations, evaluation harnesses, etc. should configure logs
      the same way without importing FastAPI.
    - Keeps log policy versioned alongside the other observability
      knobs (tracers, filters, eventually metrics).
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

from .logging_filter import TraceIdLoggingFilter

# ── Formatters ────────────────────────────────────────────────────

# Human-readable, what we've been using since D2.4.
_TEXT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | trace=%(trace_id)s | "
    "%(name)s | %(message)s"
)
_TEXT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def _build_json_formatter() -> logging.Formatter:
    """Build a python-json-logger formatter (lazy import, optional dep)."""
    try:
        from pythonjsonlogger import jsonlogger  # noqa: WPS433
    except ImportError as e:
        # Fall back loudly rather than silently — JSON was explicitly
        # requested via env; misconfiguration deserves attention.
        raise RuntimeError(
            "LOG_FORMAT=json requires `python-json-logger`. "
            "Install it (`pip install python-json-logger`) or set "
            "LOG_FORMAT=text."
        ) from e

    # The format string lists which LogRecord attributes get pulled
    # into the JSON object. The filter already injects `trace_id`.
    json_fmt = (
        "%(asctime)s %(levelname)s %(name)s %(trace_id)s %(message)s"
    )
    return jsonlogger.JsonFormatter(
        json_fmt,
        rename_fields={
            "asctime":  "timestamp",
            "levelname": "level",
            "name":     "logger",
        },
        datefmt=_TEXT_DATEFMT,
    )


def _build_text_formatter() -> logging.Formatter:
    return logging.Formatter(_TEXT_FORMAT, datefmt=_TEXT_DATEFMT)


# ── Public entry point ────────────────────────────────────────────

LogFormat = Literal["text", "json"]


def configure_logging(
    *,
    level:     str       = "INFO",
    log_format: LogFormat = "text",
) -> None:
    """Wire root logger with the requested level + formatter + trace filter.

    Replaces any existing handlers so this is safe to call after
    libraries (uvicorn, langchain) have already configured their own.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Drop any handlers libraries / a previous call set up so we don't
    # double-emit each record.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        _build_json_formatter() if log_format == "json"
        else _build_text_formatter()
    )
    handler.addFilter(TraceIdLoggingFilter())
    root.addHandler(handler)

    # Tame the noisy ones — they default to INFO and flood traces.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel("WARNING")
