"""Tests for the typed tool wrappers + spec generation in core/tools."""
from __future__ import annotations

import pytest

from core.tools import build_tool_specs, dispatch, split_tool_name


class _PoolStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def call(self, domain: str, tool: str, args: dict):
        self.calls.append((domain, tool, args))
        return {"ok": True, "domain": domain, "tool": tool}


def test_specs_listed_for_all_four_domains():
    names = {s["name"] for s in build_tool_specs()}
    # claims
    assert {"claims__get_claim_by_id", "claims__list_claims_by_submitter", "claims__get_claim_trace"} <= names
    # policies
    assert {"policies__get_policy_by_id", "policies__get_policies_by_user_id", "policies__get_coverage_rules"} <= names
    # users — no list_all_users (anti-IDOR)
    assert {"users__get_user_by_id", "users__get_user_by_email"} <= names
    assert "users__list_all_users" not in names
    # documents
    assert {"documents__parse_pdf", "documents__compute_sha256", "documents__extract_text"} <= names


def test_no_write_restricted_tool_appears():
    names = {s["name"] for s in build_tool_specs()}
    for bad in ("claims__create_claim", "claims__update_claim_status",
                "policies__update_policy", "users__create_user"):
        assert bad not in names


def test_split_tool_name_ok():
    assert split_tool_name("claims__get_claim_by_id") == ("claims", "get_claim_by_id")


@pytest.mark.parametrize("bad", ["nodelim", "__missingdomain", "domainonly__"])
def test_split_tool_name_rejects_malformed(bad):
    with pytest.raises(ValueError):
        split_tool_name(bad)


@pytest.mark.asyncio
async def test_dispatch_routes_to_pool():
    pool = _PoolStub()
    res = await dispatch(pool, "claims__get_claim_by_id", {
        "claim_id": "00000000-0000-0000-0000-000000000000",
        "caller_sub": "a@b.c", "caller_role": "ANALYST",
    })
    assert res["ok"] is True
    assert pool.calls == [("claims", "get_claim_by_id", {
        "claim_id": "00000000-0000-0000-0000-000000000000",
        "caller_sub": "a@b.c", "caller_role": "ANALYST",
    })]


@pytest.mark.asyncio
async def test_dispatch_unknown_raises():
    pool = _PoolStub()
    with pytest.raises(ValueError):
        await dispatch(pool, "claims__nuke_database", {})
