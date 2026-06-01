"""Backward-compat shim. The real LLM contract lives in `core.models`.

Kept so older imports (`from core.llm import LLMClient, LLMTurn`) still work; new code
should import from `core.models` directly. Will be removed once the codebase has fully
migrated.
"""
from __future__ import annotations

from .models.base import LLMClient, LLMTurn  # noqa: F401
