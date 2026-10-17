"""Security-critical integration tests for the claims MCP.

Coverage (Rule 5 required test matrix):
- ANALYST reads own claim → success.
- ANALYST reads another user's claim → forbidden, kind=business, no leak.
- ADMIN reads any claim → success.
- list_claims_by_submitter forces caller_sub for ANALYST regardless of submitter_sub argument.
- Invalid input (bad UUID, extra field, missing role) → business error, never internal.
- Internal failure (engine disposed mid-call) → "upstream_internal_error", no SQL/stack leaked.
"""
from __future__ import annotations

import pytest

from _common.settings import MCPSettings

pytestmark = pytest.mark.asyncio


def _settings() -> MCPSettings:
    return MCPSettings(mcp_domain="claims")  # type: ignore[call-arg]


def _build_tools_map():
    from claims.tools import build_tools  # noqa: WPS433 — late import after sys.path setup

    specs = build_tools(_settings())
    return {s.name: s for s in specs}


def _wrap(handler):
    from _common.errors import classified

    return classified(handler)


# ── ANALYST own claim ───────────────────────────────────────────────────────


async def test_analyst_reads_own_claim(claim_owned_by_analyst, fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": claim_owned_by_analyst,
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        }
    )
    assert "error" not in res, res
    assert res["claim"]["id"] == claim_owned_by_analyst
    assert res["claim"]["submitted_by_sub"] == fixture_subs["analyst"]


# ── ANALYST cross-tenant denial ─────────────────────────────────────────────


async def test_analyst_cannot_read_other_claim(claim_owned_by_other, fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": claim_owned_by_other,
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "forbidden", "kind": "business"}, res
    # critically: the response must NOT leak the actual submitted_by_sub of the other user
    assert "someone-else" not in str(res)


# ── ADMIN sees any claim ────────────────────────────────────────────────────


async def test_admin_reads_any_claim(claim_owned_by_other, fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": claim_owned_by_other,
            "caller_sub": fixture_subs["admin"],
            "caller_role": "ADMIN",
        }
    )
    assert "error" not in res, res
    assert res["claim"]["id"] == claim_owned_by_other


# ── list forces caller_sub for ANALYST ──────────────────────────────────────


async def test_list_forces_caller_sub_for_analyst(
    claim_owned_by_analyst, claim_owned_by_other, fixture_subs
):
    tools = _build_tools_map()
    # ANALYST tries to enumerate someone else's claims — the filter MUST be overridden.
    res = await _wrap(tools["list_claims_by_submitter"].handler)(
        {
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
            "submitter_sub": "someone-else@example.com",
            "limit": 50,
            "offset": 0,
        }
    )
    assert "error" not in res, res
    assert res["filtered_by_sub"] == fixture_subs["analyst"]
    assert all(c["submitted_by_sub"] == fixture_subs["analyst"] for c in res["claims"])


async def test_admin_can_target_specific_submitter(claim_owned_by_other, fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["list_claims_by_submitter"].handler)(
        {
            "caller_sub": fixture_subs["admin"],
            "caller_role": "ADMIN",
            "submitter_sub": "someone-else@example.com",
            "limit": 50,
            "offset": 0,
        }
    )
    assert "error" not in res, res
    assert res["filtered_by_sub"] == "someone-else@example.com"
    assert any(c["id"] == claim_owned_by_other for c in res["claims"])


# ── invalid input ───────────────────────────────────────────────────────────


async def test_bad_uuid_is_business_error(fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": "not-a-uuid",
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        }
    )
    assert res["kind"] == "business"
    # the safe path: bad UUID is rejected before any SQL is issued
    assert "claim" not in res


async def test_extra_field_rejected(fixture_subs):
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": "00000000-0000-0000-0000-000000000000",
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
            "drop_table": "claims",  # extra=forbid must reject this
        }
    )
    assert res["kind"] == "business"


async def test_missing_role_rejected():
    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {"claim_id": "00000000-0000-0000-0000-000000000000", "caller_sub": "x@y.z"}
    )
    assert res["kind"] == "business"


# ── internal failure does not leak ──────────────────────────────────────────


async def test_internal_error_does_not_leak_sql(monkeypatch, fixture_subs):
    """Force a RuntimeError inside the repository and assert the response is sanitized."""
    from claims import repository as repo

    async def boom(*args, **kwargs):
        raise RuntimeError("ERROR: relation 'super_secret_table' does not exist at line 42")

    monkeypatch.setattr(repo, "fetch_claim_by_id", boom)

    tools = _build_tools_map()
    res = await _wrap(tools["get_claim_by_id"].handler)(
        {
            "claim_id": "00000000-0000-0000-0000-000000000000",
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        }
    )
    assert res == {"error": "upstream_internal_error", "kind": "internal"}
    assert "super_secret_table" not in str(res)
    assert "ERROR" not in res["error"]
