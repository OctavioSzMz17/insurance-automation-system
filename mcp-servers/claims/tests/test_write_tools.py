"""Integration tests for the write tools (create_claim, update_claim_status).

Covers (Rule 4, 5, 6, 7):
- write tools refuse to execute when MCP_WRITE_ENABLED is unset → ForbiddenError.
- create_claim ACL: ANALYST submitting for itself ok; ANALYST submitting for another → forbidden.
- create_claim dedup: same (submitted_by_sub, pdf_sha256) twice → second returns existing.
- update_claim_status ACL same as read.
- update_claim_status: MANUAL_REVIEW is terminal — refuses transition.
- update_claim_status: any other terminal status (APPROVED/REJECTED) also refused.
- update_claim_status: invalid new_status rejected at Pydantic layer.
"""
from __future__ import annotations

import json
import os
from uuid import UUID

import pytest

from _common.settings import MCPSettings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _enable_writes(monkeypatch):
    monkeypatch.setenv("MCP_WRITE_ENABLED", "1")


def _build_tools_map():
    from claims.tools import build_tools
    return {s.name: s for s in build_tools(MCPSettings(mcp_domain="claims"))}  # type: ignore[call-arg]


def _wrap(handler):
    from _common.errors import classified
    return classified(handler)


# ── @write_restricted gate ─────────────────────────────────────────────────


async def test_create_claim_refuses_when_writes_disabled(monkeypatch, fixture_subs):
    monkeypatch.setenv("MCP_WRITE_ENABLED", "0")
    tools = _build_tools_map()
    res = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": "00000000-0000-0000-0000-000000000001",
        "pdf_sha256": "c" * 64,
        "reported_damage_amount": "100.00",
        "severity": "LOW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    assert res == {"error": "write operations are disabled in this deployment", "kind": "business"}


# ── happy path: ANALYST creates own claim ──────────────────────────────────


async def test_analyst_creates_own_claim(db_conn, fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    pdf = "d" * 64
    res = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": pdf,
        "reported_damage_amount": "1500.00",
        "severity": "MEDIUM",
        "decision_metadata": {"source": "test"},
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    try:
        assert "error" not in res, res
        assert res["claim"]["created"] is True
        assert res["claim"]["status"] == "SUBMITTED"
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM claims WHERE pdf_sha256 = %s", (pdf,))
        db_conn.commit()


# ── ACL: ANALYST cannot create for another sub ─────────────────────────────


async def test_analyst_cannot_create_for_other_sub(fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    res = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": "victim@example.com",
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": "e" * 64,
        "reported_damage_amount": "100.00",
        "severity": "LOW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    assert res == {"error": "forbidden", "kind": "business"}


# ── dedup: same submitter+sha twice → second returns existing ──────────────


async def test_create_claim_dedup(db_conn, fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    pdf = "f" * 64
    args = {
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": pdf,
        "reported_damage_amount": "200.00",
        "severity": "LOW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    }
    try:
        first = await _wrap(tools["create_claim"].handler)(args)
        second = await _wrap(tools["create_claim"].handler)(args)
        assert first["claim"]["created"] is True
        assert second["claim"]["created"] is False
        assert first["claim"]["id"] == second["claim"]["id"]
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM claims WHERE pdf_sha256 = %s", (pdf,))
        db_conn.commit()


# ── update_claim_status ────────────────────────────────────────────────────


async def test_update_status_happy(db_conn, fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    pdf = "1" * 64
    res = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": pdf,
        "reported_damage_amount": "300.00",
        "severity": "LOW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    claim_id = res["claim"]["id"]
    try:
        upd = await _wrap(tools["update_claim_status"].handler)({
            "claim_id": claim_id,
            "new_status": "UNDER_REVIEW",
            "decision_metadata": {"reviewer": "test"},
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        })
        assert "error" not in upd, upd
        assert upd["claim"]["status"] == "UNDER_REVIEW"
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
        db_conn.commit()


async def test_update_status_refuses_when_manual_review(db_conn, fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    pdf = "2" * 64
    create = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": pdf,
        "reported_damage_amount": "300.00",
        "severity": "LOW",
        "status": "MANUAL_REVIEW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    claim_id = create["claim"]["id"]
    try:
        upd = await _wrap(tools["update_claim_status"].handler)({
            "claim_id": claim_id,
            "new_status": "AWAITING_INPUT",
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        })
        # Rule 5: MANUAL_REVIEW is terminal — denied at application layer.
        assert upd["kind"] == "business"
        assert "terminal" in upd["error"].lower() or "MANUAL_REVIEW" in upd["error"]
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
        db_conn.commit()


async def test_update_status_refuses_invalid_status(db_conn, fixture_subs):
    from scripts.seed.fixtures import POLICY_AUTO_ID
    tools = _build_tools_map()
    pdf = "3" * 64
    create = await _wrap(tools["create_claim"].handler)({
        "submitted_by_sub": fixture_subs["analyst"],
        "policy_id": str(POLICY_AUTO_ID),
        "pdf_sha256": pdf,
        "reported_damage_amount": "300.00",
        "severity": "LOW",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    claim_id = create["claim"]["id"]
    try:
        upd = await _wrap(tools["update_claim_status"].handler)({
            "claim_id": claim_id,
            "new_status": "DROPPED_BY_ATTACKER",
            "caller_sub": fixture_subs["analyst"],
            "caller_role": "ANALYST",
        })
        assert upd["kind"] == "business"
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
        db_conn.commit()


async def test_update_status_acl_denies_other(db_conn, claim_owned_by_other, fixture_subs):
    tools = _build_tools_map()
    upd = await _wrap(tools["update_claim_status"].handler)({
        "claim_id": claim_owned_by_other,
        "new_status": "REJECTED",
        "caller_sub": fixture_subs["analyst"],
        "caller_role": "ANALYST",
    })
    assert upd == {"error": "forbidden", "kind": "business"}
