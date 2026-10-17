"""Security-critical integration tests for the policies MCP."""
from __future__ import annotations

import pytest

from _common.settings import MCPSettings

pytestmark = pytest.mark.asyncio


def _build_tools_map():
    from policies.tools import build_tools

    settings = MCPSettings(mcp_domain="policies")  # type: ignore[call-arg]
    return {s.name: s for s in build_tools(settings)}


def _wrap(handler):
    from _common.errors import classified

    return classified(handler)


# ── happy path ──────────────────────────────────────────────────────────────


async def test_owner_reads_own_policy(fixture_ids):
    """Non-ADMIN callers may read a policy only when they own it.

    The seeded policies belong to a client (migration 0004), so ownership is asserted
    with that client's sub — an analyst does not own anything here.
    """
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": str(fixture_ids["policy_auto_id"]),
            "caller_sub": fixture_ids["client_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert res["policy"]["id"] == str(fixture_ids["policy_auto_id"])
    assert "owner_email" not in res["policy"]  # never surfaced


async def test_admin_reads_any_policy(other_user_with_policy, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": other_user_with_policy["policy_id"],
            "caller_sub": fixture_ids["admin_sub"],
            "caller_role": "ADMIN",
        }
    )
    assert "error" not in res, res
    assert res["policy"]["id"] == other_user_with_policy["policy_id"]


# ── cross-tenant denial (anti-IDOR on policies) ─────────────────────────────


async def test_analyst_cannot_read_other_users_policy(other_user_with_policy, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": other_user_with_policy["policy_id"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}, res
    # the victim's email and id must never leak
    assert other_user_with_policy["user_email"] not in str(res)
    assert other_user_with_policy["user_id"] not in str(res)


async def test_analyst_cannot_list_other_users_policies(other_user_with_policy, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policies_by_user_id"].handler)(
        {
            "user_id": other_user_with_policy["user_id"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}, res


async def test_admin_can_list_other_users_policies(other_user_with_policy, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policies_by_user_id"].handler)(
        {
            "user_id": other_user_with_policy["user_id"],
            "caller_sub": fixture_ids["admin_sub"],
            "caller_role": "ADMIN",
        }
    )
    assert "error" not in res, res
    assert any(p["id"] == other_user_with_policy["policy_id"] for p in res["policies"])


# ── coverage rules tool inherits ACL ────────────────────────────────────────


async def test_coverage_rules_acl_denies_other(other_user_with_policy, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_coverage_rules"].handler)(
        {
            "policy_id": other_user_with_policy["policy_id"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}


async def test_coverage_rules_returns_jsonb(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_coverage_rules"].handler)(
        {
            "policy_id": str(fixture_ids["policy_auto_id"]),
            "caller_sub": fixture_ids["client_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    codes = {r["code"] for r in res["coverage_rules"]}
    assert "COLLISION" in codes


# ── input hardening ─────────────────────────────────────────────────────────


async def test_extra_field_rejected(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": str(fixture_ids["policy_auto_id"]),
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
            "shell": "rm -rf /",  # forbid must reject
        }
    )
    assert res["kind"] == "business"


async def test_bad_uuid_rejected(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": "definitely-not-a-uuid",
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    assert "policy" not in res


# ── not-found surfaces as business, not internal ────────────────────────────


async def test_not_found_is_business(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": "00000000-0000-0000-0000-000000000000",
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    assert "policy not found" in res["error"]


# ── internal error does not leak ────────────────────────────────────────────


async def test_internal_error_does_not_leak(monkeypatch, fixture_ids):
    from policies import repository as repo

    async def boom(*a, **k):
        raise RuntimeError("psql: relation 'internal_billing' does not exist")

    monkeypatch.setattr(repo, "fetch_policy_with_owner", boom)
    tools = _build_tools_map()
    res = await _wrap(tools["get_policy_by_id"].handler)(
        {
            "policy_id": str(fixture_ids["policy_auto_id"]),
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "upstream_internal_error", "kind": "internal"}
    assert "internal_billing" not in str(res)
