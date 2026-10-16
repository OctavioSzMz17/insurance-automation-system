"""Strict schemas must survive a JSON round trip (regression).

Why this exists: the HITL handoff stores the analyst proposal, triage report and auditor
decision in Redis as JSON and rebuilds them in `/audit` and `/confirm`. The models are
`strict=True`, so rebuilding them with `Cls(**json_dict)` fails — JSON has no UUID,
datetime or Decimal type, and strict mode refuses to parse a string into one. That broke
both endpoints with a 500 while every unit test stayed green, because no test crossed the
serialization boundary. `from_stored` is the supported way across it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.common.enums import SeverityLabel
from packages.common.schemas.claim import ClaimDecision, ClaimProposal
from packages.common.schemas.rehydrate import from_stored
from packages.common.schemas.triage import TriageReport


def _proposal() -> ClaimProposal:
    now = datetime.now(tz=timezone.utc)
    return ClaimProposal(
        proposal_id=uuid4(),
        pipeline_id=uuid4(),
        actor="analyst",
        payload_sha256="a" * 64,
        signature_hex="b" * 64,
        issued_at=now,
        expires_at=now,
    )


def _triage() -> TriageReport:
    return TriageReport(
        pipeline_id=uuid4(),
        policy_id=uuid4(),
        pdf_sha256="c" * 64,
        reported_damage_amount=Decimal("1234.56"),
        severity=SeverityLabel.MEDIUM,
        summary="Colision frontal",
        evidence_quotes=("danos por $1234.56",),
    )


def _decision() -> ClaimDecision:
    return ClaimDecision(
        claim_id=uuid4(),
        decision="APPROVED",
        reason="Cobertura vigente y monto respaldado",
        evidence=("linea 12 del reporte",),
        model="stub",
        model_version="stub",
        decided_at=datetime.now(tz=timezone.utc),
    )


def _through_redis(model) -> dict:
    """Exactly what HITLStore does: dump to JSON, store, load back."""
    return json.loads(json.dumps(model.model_dump(mode="json")))


@pytest.mark.parametrize(
    ("factory", "cls"),
    [(_proposal, ClaimProposal), (_triage, TriageReport), (_decision, ClaimDecision)],
    ids=["ClaimProposal", "TriageReport", "ClaimDecision"],
)
def test_from_stored_rebuilds_model(factory, cls) -> None:
    original = factory()
    restored = from_stored(cls, _through_redis(original))
    assert restored == original


@pytest.mark.parametrize(
    ("factory", "cls"),
    [(_proposal, ClaimProposal), (_triage, TriageReport), (_decision, ClaimDecision)],
    ids=["ClaimProposal", "TriageReport", "ClaimDecision"],
)
def test_strict_construction_still_rejects_json(factory, cls) -> None:
    """Documents *why* from_stored is required — strict mode refuses JSON scalars."""
    with pytest.raises(ValidationError):
        cls(**_through_redis(factory()))


def test_from_stored_still_enforces_field_constraints() -> None:
    """Non-strict is not permissive: a malformed digest is still rejected."""
    payload = _through_redis(_proposal())
    payload["payload_sha256"] = "not-a-sha256"
    with pytest.raises(ValidationError):
        from_stored(ClaimProposal, payload)


def test_from_stored_still_forbids_unknown_fields() -> None:
    payload = _through_redis(_proposal())
    payload["injected"] = "surprise"
    with pytest.raises(ValidationError):
        from_stored(ClaimProposal, payload)
