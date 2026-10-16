"""The canonical structured JSON logger (Rule 9: one logger, no `print()` anywhere).

Rule 9 names this module as the import path for logging, so the implementation lives
here. `mcp-servers/_common/logging.py` re-exports it for the MCP servers, which predate
this package — same functions, one implementation.

Every field passed via `extra=` is merged into the JSON object, which is what makes the
log queryable: `event`, `pipeline_id`, `trace_id` and friends become top-level keys
rather than being buried in a formatted message.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info", "thread",
    "threadName", "taskName",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_") or k in _RESERVED:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, default=str)


_CONFIGURED = False


def configure(level: str | None = None) -> None:
    """Idempotent root logger configuration. Honors LOG_LEVEL env."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    root.setLevel(lvl)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure()
    return logging.getLogger(name)


__all__ = ["configure", "get_logger"]
