"""Security-critical integration tests for the users MCP (anti-IDOR focus)."""
from __future__ import annotations

import pytest

from _common.settings import MCPSettings

pytestmark = pytest.mark.asyncio


def _build_tools_map():
    from users.tools import build_tools

    settings = MCPSettings(mcp_domain="users")  # type: ignore[call-arg]
    return {s.name: s for s in build_tools(settings)}


def _wrap(handler):
    from _common.errors import classified

    return classified(handler)


# ── happy path ──────────────────────────────────────────────────────────────


async def test_analyst_reads_self_by_id(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": str(fixture_ids["analyst_id"]),
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert res["user"]["email"] == fixture_ids["analyst_sub"]


async def test_analyst_reads_self_by_email(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_email"].handler)(
        {
            "email": fixture_ids["analyst_sub"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert res["user"]["email"] == fixture_ids["analyst_sub"]


async def test_admin_reads_any_user(other_user, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": other_user["id"],
            "caller_sub": fixture_ids["admin_sub"],
            "caller_role": "ADMIN",
        }
    )
    assert "error" not in res, res
    assert res["user"]["email"] == other_user["email"]


# ── anti-IDOR by id ─────────────────────────────────────────────────────────


async def test_analyst_cannot_read_other_user_by_id(other_user, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": other_user["id"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}, res
    # The victim's email/full_name must not leak even in error messages.
    assert other_user["email"] not in str(res)


# ── anti-IDOR by email ──────────────────────────────────────────────────────


async def test_analyst_cannot_read_other_user_by_email(other_user, fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_email"].handler)(
        {
            "email": other_user["email"],
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}, res


# ── enumeration via nonexistent email is also denied (no side channel) ──────


async def test_analyst_email_lookup_denies_nonexistent(fixture_ids):
    """If we let 'user not found' through for arbitrary emails, an attacker could enumerate
    accounts by probing which emails return 'forbidden' vs 'not found'. We deny both shapes.
    """
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_email"].handler)(
        {
            "email": "nobody-here@example.com",
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}


async def test_analyst_id_lookup_for_nonexistent_does_not_distinguish(fixture_ids):
    """For id-based lookup, ANALYST querying a nonexistent id returns 'user not found'
    (kind:business). That is still safe because UUIDs are not enumerable by guessing,
    and the existence side channel for UUIDs is acceptable per Rule 7's threat model.
    """
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"


# ── there is no list_all_users tool ─────────────────────────────────────────


async def test_no_list_all_users_tool_is_exposed():
    tools = _build_tools_map()
    assert "list_all_users" not in tools
    assert "list_users" not in tools


# ── input hardening ─────────────────────────────────────────────────────────


async def test_extra_field_rejected(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": str(fixture_ids["analyst_id"]),
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
            "select_all": True,
        }
    )
    assert res["kind"] == "business"


async def test_bad_email_rejected(fixture_ids):
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_email"].handler)(
        {
            "email": "not-an-email",
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"


# ── internal errors do not leak ─────────────────────────────────────────────


async def test_internal_error_does_not_leak(monkeypatch, fixture_ids):
    from users import repository as repo

    async def boom(*a, **k):
        raise RuntimeError("password='SUPER_SECRET' in connection string")

    monkeypatch.setattr(repo, "fetch_user_by_id", boom)
    tools = _build_tools_map()
    res = await _wrap(tools["get_user_by_id"].handler)(
        {
            "user_id": str(fixture_ids["analyst_id"]),
            "caller_sub": fixture_ids["analyst_sub"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "upstream_internal_error", "kind": "internal"}
    assert "SUPER_SECRET" not in str(res)
