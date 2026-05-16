"""Chat model factory with optional local→external fallback.

Reads three generic variables from .env:
    LLM_PROVIDER           — provider id  (claude | openai | ollama | gemini)
    LLM_PROVIDER_API_KEY   — API key for that provider (blank for local)
    MODEL_NAME             — exact model identifier on that provider

Optional local fallback (set both or none):
    LLM_LOCAL_PROVIDER     — local provider id (typically "ollama")
    LLM_LOCAL_MODEL_NAME   — local model identifier

If LLM_LOCAL_PROVIDER is set, every LLM call tries local FIRST and
falls back to the primary provider on failure.

Rules applied:
    Rule 8  — All keys / URLs from .env, never hardcoded
    Rule 9  — Structured logging on provider selection
    Rule 11 — Type hints everywhere
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

load_dotenv()

logger = logging.getLogger(__name__)


# ── Provider builders (lazy imports keep optional deps optional) ──

def _build_claude(model_name: str, api_key: str, **kwargs: Any) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic
    if not api_key:
        raise ValueError("LLM_PROVIDER_API_KEY is required for provider 'claude'")
    return ChatAnthropic(model=model_name, api_key=api_key, **kwargs)


def _build_openai(model_name: str, api_key: str, **kwargs: Any) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    if not api_key:
        raise ValueError("LLM_PROVIDER_API_KEY is required for provider 'openai'")
    return ChatOpenAI(model=model_name, api_key=api_key, **kwargs)


def _build_ollama(model_name: str, api_key: str, **kwargs: Any) -> BaseChatModel:
    # api_key is ignored for local providers
    from langchain_ollama import ChatOllama
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(model=model_name, base_url=base_url, **kwargs)


_BUILDERS = {
    "claude": _build_claude,
    "openai": _build_openai,
    "ollama": _build_ollama,
}


# ── Public factory ────────────────────────────────────────────────

def _build_single_model(provider: str, model_name: str, api_key: str) -> BaseChatModel:
    """Build one chat model from provider + model_name + api_key."""
    if provider not in _BUILDERS:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Supported: {list(_BUILDERS.keys())}"
        )

    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
    max_tokens  = int(os.environ.get("LLM_MAX_TOKENS", "2000"))

    logger.info(
        f"Building chat model | provider={provider} "
        f"model={model_name} temp={temperature}"
    )
    return _BUILDERS[provider](
        model_name,
        api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_chat_model() -> BaseChatModel:
    """Return a ChatModel built from .env, with optional local→external fallback.

    Returns:
        A LangChain BaseChatModel. If LLM_LOCAL_PROVIDER is set, the
        returned model is `local.with_fallbacks([primary])` — meaning
        every invoke tries local first and silently falls back on failure.

    Raises:
        ValueError: If LLM_PROVIDER is missing or unknown, or if the
                    required API key is not set.
    """
    primary_provider = os.environ.get("LLM_PROVIDER", "claude").lower()
    primary_key      = os.environ.get("LLM_PROVIDER_API_KEY", "")
    primary_model    = os.environ.get("MODEL_NAME", "")
    if not primary_model:
        raise ValueError("MODEL_NAME is not set in .env")

    primary = _build_single_model(primary_provider, primary_model, primary_key)

    local_provider = os.environ.get("LLM_LOCAL_PROVIDER", "").lower()
    if not local_provider:
        logger.info(
            f"Chat model resolved | provider={primary_provider} "
            f"model={primary_model} fallback=none"
        )
        return primary

    local_model_name = os.environ.get("LLM_LOCAL_MODEL_NAME", "")
    if not local_model_name:
        raise ValueError(
            "LLM_LOCAL_PROVIDER is set but LLM_LOCAL_MODEL_NAME is empty"
        )

    local = _build_single_model(local_provider, local_model_name, api_key="")
    logger.info(
        f"Chat model resolved | primary={local_provider}/{local_model_name} "
        f"fallback={primary_provider}/{primary_model}"
    )
    return local.with_fallbacks([primary])
