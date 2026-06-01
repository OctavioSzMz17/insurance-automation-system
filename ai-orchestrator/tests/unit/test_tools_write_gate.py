"""Verify the orchestrator's tool registry hides writes from the LLM and refuses dispatch.

Defense in depth (Rule 4):
1. build_tool_specs() default omits @write_restricted tools — the LLM can't see them.
2. dispatch() refuses write tools unless allow_writes=True.
"""
from __future__ import annotations

import pytest

from core.tools import build_tool_specs, dispatch, is_write_restricted
from packages.common.exceptions import ForbiddenError


def test_writes_hidden_from_llm_by_default():
    names = {s["name"] for s in build_tool_specs()}
    assert "claims__create_claim" not in names
    assert "claims__update_claim_status" not in names


def test_writes_visible_when_explicitly_requested():
    names = {s["name"] for s in build_tool_specs(include_writes=True)}
    assert "claims__create_claim" in names
    assert "claims__update_claim_status" in names


def test_is_write_restricted_introspection():
    assert is_write_restricted("claims__create_claim") is True
    assert is_write_restricted("claims__update_claim_status") is True
    assert is_write_restricted("claims__get_claim_by_id") is False
    assert is_write_restricted("policies__get_policy_by_id") is False
    assert is_write_restricted("users__get_user_by_email") is False


class _RecordingPool:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def call(self, domain, tool, args):
        self.calls.append((domain, tool, args))
        return {"ok": True}


@pytest.mark.asyncio
async def test_dispatch_refuses_write_without_allow_flag():
    pool = _RecordingPool()
    with pytest.raises(ForbiddenError):
        await dispatch(pool, "claims__create_claim", {"caller_sub": "x", "caller_role": "ADMIN"})
    assert pool.calls == []  # never reached the pool


@pytest.mark.asyncio
async def test_dispatch_allows_write_with_allow_flag():
    pool = _RecordingPool()
    await dispatch(pool, "claims__create_claim",
                   {"caller_sub": "x", "caller_role": "ADMIN"}, allow_writes=True)
    assert pool.calls and pool.calls[0][1] == "create_claim"


@pytest.mark.asyncio
async def test_dispatch_refuses_unknown_tool():
    pool = _RecordingPool()
    with pytest.raises(ValueError):
        await dispatch(pool, "claims__nuke_database", {})
