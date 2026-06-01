"""ClaimProposal HMAC sign + verify (Rule 6, Rule 9).

The proposal is the handoff object between the analyst (which generates) and the future
HITL gate / auditor (which consumes). It is signed so that:
  - Nothing between generation and consumption can tamper with the payload silently.
  - A leaked proposal_id is useless without the HMAC key (a separate secret from MCP auth).
  - The TTL bounds the replay window.

Signature scheme:
    canonical = b"|".join([
        proposal_id_bytes,
        pipeline_id_bytes,
        actor.encode(),
        payload_sha256.encode(),
        issued_at_iso.encode(),
        expires_at_iso.encode(),
    ])
    signature = HMAC-SHA256(HMAC_PROPOSAL_KEY, canonical)[:16].hex()   # 128-bit minimum

Truncation to 16 bytes (128 bits) is documented as the Rule 9 minimum. compare_digest
gives constant-time equality.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from packages.common.exceptions import ValidationFailure
from packages.common.schemas.claim import ClaimProposal

_SIG_BYTES: Final[int] = 32  # full SHA-256 (256-bit). Rule 9 mandates 128-bit minimum; we use the full digest.


def _canonical_bytes(
    *,
    proposal_id: UUID,
    pipeline_id: UUID,
    actor: str,
    payload_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
) -> bytes:
    parts = [
        str(proposal_id).encode(),
        str(pipeline_id).encode(),
        actor.encode(),
        payload_sha256.encode(),
        issued_at.isoformat().encode(),
        expires_at.isoformat().encode(),
    ]
    return b"|".join(parts)


def _hash_payload(payload: dict[str, Any]) -> str:
    """SHA-256 over a JSON-canonical (sorted keys, no whitespace) serialization."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _sign(canonical: bytes, key: str) -> str:
    mac = hmac.new(key.encode(), canonical, hashlib.sha256).digest()
    # _SIG_BYTES == 32 == full SHA-256; the slice is a no-op kept for explicitness.
    return mac[:_SIG_BYTES].hex()


def build_proposal(
    *,
    pipeline_id: UUID,
    actor: Literal["analyst", "auditor"],
    payload: dict[str, Any],
    key: str,
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> ClaimProposal:
    """Construct a signed ClaimProposal for the given payload.

    The payload itself is NOT included in the returned object — only its sha256. The
    consumer is expected to re-receive the payload through the same channel (or look it up
    by pipeline_id) and re-compute the sha256 to confirm match.
    """
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    proposal_id = uuid4()
    payload_sha256 = _hash_payload(payload)
    canonical = _canonical_bytes(
        proposal_id=proposal_id,
        pipeline_id=pipeline_id,
        actor=actor,
        payload_sha256=payload_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    signature_hex = _sign(canonical, key)
    return ClaimProposal(
        proposal_id=proposal_id,
        pipeline_id=pipeline_id,
        actor=actor,
        payload_sha256=payload_sha256,
        signature_hex=signature_hex,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def verify_proposal(
    proposal: ClaimProposal,
    *,
    payload: dict[str, Any],
    key: str,
    now: datetime | None = None,
) -> None:
    """Raise ValidationFailure on any verification failure. Constant-time comparison."""
    current = now or datetime.now(timezone.utc)
    if current >= proposal.expires_at:
        raise ValidationFailure("proposal expired")
    expected_payload_hash = _hash_payload(payload)
    if not hmac.compare_digest(expected_payload_hash, proposal.payload_sha256):
        raise ValidationFailure("payload digest mismatch")
    canonical = _canonical_bytes(
        proposal_id=proposal.proposal_id,
        pipeline_id=proposal.pipeline_id,
        actor=proposal.actor,
        payload_sha256=proposal.payload_sha256,
        issued_at=proposal.issued_at,
        expires_at=proposal.expires_at,
    )
    expected_sig = _sign(canonical, key)
    if not hmac.compare_digest(expected_sig, proposal.signature_hex):
        raise ValidationFailure("signature mismatch")
