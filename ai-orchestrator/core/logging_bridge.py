"""Re-exposes the canonical structured logger so orchestrator code uses one logger.

The implementation lives in `observability/logging.py` (Rule 9). This bridge is kept so
the existing `from core.logging_bridge import get_logger` call sites keep working; new
code should import from `observability` directly.
"""
from __future__ import annotations

from observability.logging import configure, get_logger  # noqa: F401
