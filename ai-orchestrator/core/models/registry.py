"""Tier → provider → LLMClient resolution.

Parses `provider:model_id` strings, builds the right adapter on demand. Caches by
(provider, model_id) so the SDK client is reused across analyst runs.

Test seam: `register_test_client(name, client)` lets tests inject a stub by name and
.env can point a tier at `stub:<name>` to bypass real providers entirely.
"""
from __future__ import annotations

import os
from typing import Final

from .base import LLMClient

_VALID_PROVIDERS: Final[frozenset[str]] = frozenset({"anthropic", "openai", "gemini", "local", "stub"})

# Caches
_clients: dict[tuple[str, str], LLMClient] = {}
_stubs: dict[str, LLMClient] = {}


def _parse(spec: str) -> tuple[str, str]:
    """'anthropic:claude-sonnet-4-6' → ('anthropic', 'claude-sonnet-4-6')."""
    if ":" not in spec:
        raise ValueError(f"model spec must be 'provider:model_id', got {spec!r}")
    provider, _, model_id = spec.partition(":")
    provider = provider.strip().lower()
    model_id = model_id.strip()
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; valid: {sorted(_VALID_PROVIDERS)}")
    if not model_id:
        raise ValueError(f"empty model_id in spec {spec!r}")
    return provider, model_id


def build_client(spec: str) -> LLMClient:
    """Build (or reuse cached) LLMClient for the given 'provider:model_id' spec."""
    provider, model_id = _parse(spec)
    key = (provider, model_id)
    if key in _clients:
        return _clients[key]
    if provider == "stub":
        client = _stubs.get(model_id)
        if client is None:
            raise RuntimeError(f"stub model {model_id!r} not registered (call register_test_client first)")
        _clients[key] = client
        return client
    if provider == "anthropic":
        from .providers.anthropic_client import AnthropicClient
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("PLACEHOLDER"):
            raise RuntimeError("ANTHROPIC_API_KEY is required for anthropic: models")
        client = AnthropicClient(api_key=api_key, model=model_id)
    elif provider == "openai":
        from .providers.openai_client import OpenAIClient
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key or api_key.startswith("PLACEHOLDER"):
            raise RuntimeError("OPENAI_API_KEY is required for openai: models")
        client = OpenAIClient(api_key=api_key, model=model_id)
    elif provider == "gemini":
        from .providers.gemini_client import GeminiClient
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key or api_key.startswith("PLACEHOLDER"):
            raise RuntimeError("GEMINI_API_KEY is required for gemini: models")
        client = GeminiClient(api_key=api_key, model=model_id)
    elif provider == "local":
        from .providers.openai_client import OpenAIClient
        base_url = os.environ.get("LOCAL_BASE_URL", "")
        if not base_url:
            raise RuntimeError("LOCAL_BASE_URL is required for local: models (OpenAI-compatible endpoint)")
        api_key = os.environ.get("LOCAL_API_KEY", "not-needed")
        client = OpenAIClient(api_key=api_key, model=model_id, base_url=base_url)
    else:
        raise RuntimeError(f"unhandled provider: {provider}")
    _clients[key] = client
    return client


def get_model(tier: str) -> LLMClient:
    """Resolve a tier name ('low'/'medium'/'high') to a configured LLMClient via .env."""
    tier = tier.lower().strip()
    env_var = f"{tier.upper()}_MODEL"
    spec = os.environ.get(env_var, "")
    if not spec:
        raise RuntimeError(f"{env_var} is not set; cannot resolve tier {tier!r}")
    return build_client(spec)


def register_test_client(name: str, client: LLMClient) -> None:
    """Test seam. Tier can be `stub:<name>` in env, returns the registered client."""
    _stubs[name] = client


def reset_for_tests() -> None:
    """Clear all caches — used by test fixtures to isolate runs."""
    _clients.clear()
    _stubs.clear()
