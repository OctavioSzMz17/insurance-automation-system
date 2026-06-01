"""Tests for the multi-provider model registry."""
from __future__ import annotations

import pytest

from core.models import register_test_client
from core.models.registry import _parse, build_client, get_model, reset_for_tests


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_for_tests()
    yield
    reset_for_tests()


def test_parse_valid_spec():
    assert _parse("anthropic:claude-sonnet-4-6") == ("anthropic", "claude-sonnet-4-6")
    assert _parse("openai:gpt-4o") == ("openai", "gpt-4o")
    assert _parse("gemini:gemini-2.0-flash") == ("gemini", "gemini-2.0-flash")
    assert _parse("local:llama3.1:8b") == ("local", "llama3.1:8b")  # model ids can contain ':'
    assert _parse("stub:fast") == ("stub", "fast")


@pytest.mark.parametrize("bad", ["no-colon", "unknown:model", "anthropic:", ":model", ""])
def test_parse_invalid_specs_rejected(bad):
    with pytest.raises(ValueError):
        _parse(bad)


def test_anthropic_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_client("anthropic:claude-sonnet-4-6")


def test_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_client("openai:gpt-4o")


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_client("gemini:gemini-2.0-flash")


def test_local_requires_base_url(monkeypatch):
    monkeypatch.delenv("LOCAL_BASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        build_client("local:llama3.1")


def test_stub_must_be_registered_first():
    with pytest.raises(RuntimeError):
        build_client("stub:unknown")


def test_stub_roundtrip():
    class FakeClient:
        model = "fake"
    register_test_client("fast", FakeClient())
    client = build_client("stub:fast")
    assert client.model == "fake"
    # cached
    assert build_client("stub:fast") is client


def test_get_model_reads_env(monkeypatch):
    class FakeClient:
        model = "stub-medium"
    register_test_client("medium-stub", FakeClient())
    monkeypatch.setenv("MEDIUM_MODEL", "stub:medium-stub")
    client = get_model("medium")
    assert client.model == "stub-medium"


def test_get_model_missing_env_raises(monkeypatch):
    monkeypatch.delenv("LOW_MODEL", raising=False)
    with pytest.raises(RuntimeError):
        get_model("low")


def test_switch_provider_only_changes_env(monkeypatch):
    """The whole point: changing MEDIUM_MODEL in env switches the provider, no code change."""
    class A:
        model = "anthropic-stub"
    class B:
        model = "openai-stub"
    register_test_client("a", A())
    register_test_client("b", B())
    monkeypatch.setenv("MEDIUM_MODEL", "stub:a")
    assert get_model("medium").model == "anthropic-stub"
    monkeypatch.setenv("MEDIUM_MODEL", "stub:b")
    assert get_model("medium").model == "openai-stub"
