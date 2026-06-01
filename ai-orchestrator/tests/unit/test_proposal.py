"""HMAC proposal sign + verify tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core import proposal
from packages.common.exceptions import ValidationFailure

_KEY = "x" * 64
_ALT_KEY = "y" * 64


def _payload():
    return {
        "policy_id": "abc",
        "reported_damage_amount": "1500.00",
        "severity": "MEDIUM",
        "summary": "ok",
    }


def test_roundtrip_ok():
    p = proposal.build_proposal(
        pipeline_id=uuid4(), actor="analyst", payload=_payload(), key=_KEY, ttl_seconds=300
    )
    proposal.verify_proposal(p, payload=_payload(), key=_KEY)


def test_payload_tampering_detected():
    p = proposal.build_proposal(
        pipeline_id=uuid4(), actor="analyst", payload=_payload(), key=_KEY
    )
    bad = dict(_payload(), severity="CRITICAL")
    with pytest.raises(ValidationFailure):
        proposal.verify_proposal(p, payload=bad, key=_KEY)


def test_wrong_key_detected():
    p = proposal.build_proposal(
        pipeline_id=uuid4(), actor="analyst", payload=_payload(), key=_KEY
    )
    with pytest.raises(ValidationFailure):
        proposal.verify_proposal(p, payload=_payload(), key=_ALT_KEY)


def test_expired_rejected():
    past = datetime.now(timezone.utc) - timedelta(seconds=600)
    p = proposal.build_proposal(
        pipeline_id=uuid4(), actor="analyst", payload=_payload(), key=_KEY,
        ttl_seconds=60, now=past,
    )
    with pytest.raises(ValidationFailure):
        proposal.verify_proposal(p, payload=_payload(), key=_KEY)


def test_signature_length_is_full_sha256_hex():
    p = proposal.build_proposal(
        pipeline_id=uuid4(), actor="analyst", payload=_payload(), key=_KEY
    )
    assert len(p.signature_hex) == 64  # 32 bytes hex-encoded (full SHA-256)


def test_proposal_id_unique_across_calls():
    payload = _payload()
    p1 = proposal.build_proposal(pipeline_id=uuid4(), actor="analyst", payload=payload, key=_KEY)
    p2 = proposal.build_proposal(pipeline_id=uuid4(), actor="analyst", payload=payload, key=_KEY)
    assert p1.proposal_id != p2.proposal_id
    assert p1.signature_hex != p2.signature_hex  # different proposal_id → different canonical


def test_canonical_includes_actor():
    pid = uuid4()
    payload = _payload()
    pa = proposal.build_proposal(pipeline_id=pid, actor="analyst", payload=payload, key=_KEY)
    # Build a corresponding proposal with the same fields but different actor → must verify differently
    pb_canonical = proposal._canonical_bytes(  # noqa: SLF001 — testing canonical contract
        proposal_id=pa.proposal_id,
        pipeline_id=pa.pipeline_id,
        actor="auditor",
        payload_sha256=pa.payload_sha256,
        issued_at=pa.issued_at,
        expires_at=pa.expires_at,
    )
    expected_b = proposal._sign(pb_canonical, _KEY)  # noqa: SLF001
    assert expected_b != pa.signature_hex
