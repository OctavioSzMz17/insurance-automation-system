"""Model registry — choose an LLM by tier name, swap providers from .env.

Usage:
    from core.models import get_model
    llm = get_model("medium")  # tier name from .env: LOW_MODEL / MEDIUM_MODEL / HIGH_MODEL

.env contract:
    LOW_MODEL=anthropic:claude-haiku-4-5
    MEDIUM_MODEL=anthropic:claude-sonnet-4-6
    HIGH_MODEL=anthropic:claude-opus-4-7
    ANALYST_MODEL_TIER=medium

    ANTHROPIC_API_KEY=...
    OPENAI_API_KEY=...   (only required if any tier resolves to openai:)
    GEMINI_API_KEY=...   (only required if any tier resolves to gemini:)
    LOCAL_BASE_URL=...   (only required if any tier resolves to local:)
    LOCAL_API_KEY=...    (optional; many local servers ignore the key)

To change the analyst's model: edit MEDIUM_MODEL (or whatever tier is wired to the
analyst) in .env. Nothing else.
"""
from __future__ import annotations

from .base import LLMClient, LLMTurn
from .registry import build_client, get_model, register_test_client

__all__ = ["LLMClient", "LLMTurn", "build_client", "get_model", "register_test_client"]
