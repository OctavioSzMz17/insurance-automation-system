"""The deterministic verification layer — pure functions, no I/O.

These are the facts the LLM is not allowed to decide, and since the mutation handler now
refuses to persist an approval while any of them is red, they are load-bearing. The
emphasis here is the three-state contract: not applicable, could-not-verify, and a real
verdict. Collapsing the middle state into either of the others is how a verification
layer silently stops verifying.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.verification import (
    build_review_flags,
    evaluate_amount,
    evaluate_coverage,
    evaluate_identity,
    evaluate_policy_ownership,
    extract_monetary_values,
    normalize_text,
    unverified,
)

_TODAY = date(2026, 6, 15)


def _policy(status="ACTIVE", *, days_from=-90, days_to=+90, owner="owner-1"):
    return {
        "user_id": owner,
        "status": status,
        "effective_from": (_TODAY + timedelta(days=days_from)).isoformat(),
        "effective_to": (_TODAY + timedelta(days=days_to)).isoformat(),
    }


# ── coverage ─────────────────────────────────────────────────────────────────


def test_coverage_in_force():
    result = evaluate_coverage(_policy(), today=_TODAY)
    assert result["in_force"] is True


@pytest.mark.parametrize(
    ("policy", "fragment"),
    [
        (_policy(days_from=-800, days_to=-30), "termin"),      # window closed
        (_policy(days_from=+30, days_to=+400), "inicia"),      # not started
        (_policy(status="CANCELLED"), "CANCELLED"),
        (_policy(status="EXPIRED"), "EXPIRED"),
        ({}, "desconocido"),                                    # no policy data at all
    ],
)
def test_coverage_not_in_force(policy, fragment):
    result = evaluate_coverage(policy, today=_TODAY)
    assert result["in_force"] is False
    assert fragment in result["reason"]


def test_coverage_boundaries_are_inclusive():
    """A claim on the first or last day of the window is covered."""
    assert evaluate_coverage(_policy(days_from=0, days_to=+1), today=_TODAY)["in_force"] is True
    assert evaluate_coverage(_policy(days_from=-1, days_to=0), today=_TODAY)["in_force"] is True


# ── ownership (the cross-tenant check) ───────────────────────────────────────


def test_ownership_belongs_to_claimant():
    result = evaluate_policy_ownership(_policy(owner="client-7"), claimant_client_id="client-7")
    assert result["belongs"] is True


def test_ownership_rejects_another_clients_policy():
    result = evaluate_policy_ownership(_policy(owner="client-7"), claimant_client_id="client-9")
    assert result["belongs"] is False
    assert "NO pertenece" in result["reason"]


@pytest.mark.parametrize(
    ("policy", "claimant"),
    [(_policy(owner="client-7"), None), ({"status": "ACTIVE"}, "client-7")],
)
def test_ownership_unverifiable_is_not_a_pass(policy, claimant):
    result = evaluate_policy_ownership(policy, claimant_client_id=claimant)
    assert result["verified"] is False
    assert "belongs" not in result


# ── identity ─────────────────────────────────────────────────────────────────


def test_identity_matches_exact_name():
    assert evaluate_identity("Juan Perez", "Asegurado: Juan Perez")["match"] is True


def test_identity_matches_across_accents():
    """The record carries accents, the document does not — still the same person."""
    assert evaluate_identity("María García", "Asegurado: Maria Garcia")["match"] is True
    assert evaluate_identity("Maria Garcia", "Asegurado: María García")["match"] is True


def test_identity_matches_reordered_tokens():
    assert evaluate_identity("Juan Perez", "Perez, Juan (titular)")["match"] is True


def test_identity_detects_impersonation():
    result = evaluate_identity("Ana Martinez", "Asegurado: Carlos Rodriguez")
    assert result["match"] is False
    assert "suplantaci" in result["reason"]


def test_identity_without_a_name_on_record_is_unverified():
    result = evaluate_identity(None, "Asegurado: cualquiera")
    assert result["verified"] is False
    assert "match" not in result


def test_identity_against_empty_document_fails_closed():
    assert evaluate_identity("Juan Perez", "")["match"] is False


# ── amount ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reported", "text"),
    [
        ("3500.00", "danos por $3500.00"),
        ("12500.00", "total: $12,500.00"),       # thousands separator in the document
        ("999.99", "estimado 999.99 usd"),
    ],
)
def test_amount_supported_by_document(reported, text):
    assert evaluate_amount(reported, text)["supported"] is True


def test_amount_detects_a_fabricated_figure():
    result = evaluate_amount("87654.00", "danos por $1,200.00")
    assert result["supported"] is False
    assert "inventado" in result["reason"]


def test_amount_zero_is_supported():
    assert evaluate_amount("0", "sin monto declarado")["supported"] is True


@pytest.mark.parametrize("reported", [None, "", "mucho dinero"])
def test_amount_unparseable_is_unverified(reported):
    result = evaluate_amount(reported, "irrelevante")
    assert result["verified"] is False
    assert "supported" not in result


def test_extract_monetary_values_normalises_separators():
    values = extract_monetary_values("total $12,500.00 y deducible 500.")
    assert 12500.0 in values
    assert 500.0 in values


def test_normalize_text_strips_accents_and_collapses_space():
    assert normalize_text("  María   GARCÍA\n") == "maria garcia"


# ── flags: the three-state contract ──────────────────────────────────────────


def test_no_flags_when_every_check_passes():
    flags = build_review_flags(
        coverage=evaluate_coverage(_policy(), today=_TODAY),
        ownership=evaluate_policy_ownership(_policy(owner="c1"), claimant_client_id="c1"),
        identity=evaluate_identity("Juan Perez", "Asegurado: Juan Perez"),
        amount=evaluate_amount("3500.00", "danos por $3500.00"),
    )
    assert flags == []


def test_absent_checks_contribute_nothing():
    """`None` means the question does not apply to this claim."""
    assert build_review_flags() == []
    assert build_review_flags(coverage=None, ownership=None, identity=None, amount=None) == []


@pytest.mark.parametrize("block", ["coverage", "ownership", "identity", "amount"])
def test_unverified_check_raises_a_flag(block):
    """The failure mode this guards: a check that could not run must not look clean."""
    flags = build_review_flags(**{block: unverified("el MCP no respondió")})
    assert len(flags) == 1
    assert "Verificación incompleta" in flags[0]
    assert block in flags[0]


def test_each_negative_verdict_raises_its_own_flag():
    flags = build_review_flags(
        coverage=evaluate_coverage(_policy(status="CANCELLED"), today=_TODAY),
        ownership=evaluate_policy_ownership(_policy(owner="c1"), claimant_client_id="c2"),
        identity=evaluate_identity("Ana Martinez", "Asegurado: Carlos Rodriguez"),
        amount=evaluate_amount("87654.00", "danos por $1,200.00"),
    )
    assert len(flags) == 4
    joined = " ".join(flags)
    for fragment in ("no vigente", "ajena", "suplantaci", "no respaldado"):
        assert fragment in joined


def test_flag_order_is_stable():
    """The HITL screen renders these in order; keep it deterministic."""
    flags = build_review_flags(
        coverage=evaluate_coverage(_policy(status="CANCELLED"), today=_TODAY),
        ownership=evaluate_policy_ownership(_policy(owner="c1"), claimant_client_id="c2"),
    )
    assert flags[0].startswith("Póliza no vigente")
    assert flags[1].startswith("Póliza ajena")
