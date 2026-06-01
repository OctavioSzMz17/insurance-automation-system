"""Self-healing validation pipeline for analyst output (Rule 5).

Contract:
- `validate_triage_payload(payload)` returns the parsed TriageReport with deterministic
  severity injected, or raises ValidationFailure with a message the LLM can act on.
- The analyst loop uses ValidationFailure messages as feedback: it appends them as a
  tool_result and lets the model retry. The loop bounds retries at MAX_VALIDATION_RETRIES.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from packages.common.enums import SeverityLabel
from packages.common.exceptions import ValidationFailure
from packages.common.schemas.triage import TriageReport

from . import severity as severity_mod
from .sanitization import clean_summary

MAX_VALIDATION_RETRIES: Final[int] = 3


def validate_triage_payload(
    payload: dict[str, Any],
    *,
    pipeline_id: UUID,
) -> TriageReport:
    """Coerce, sanitise, validate. The LLM only supplies the substantive fields — we
    inject pipeline_id (server-known) and overwrite severity with the deterministic value.
    """
    if not isinstance(payload, dict):
        raise ValidationFailure("payload must be a JSON object")

    # Required scalars
    for required in ("policy_id", "pdf_sha256", "reported_damage_amount", "summary"):
        if required not in payload:
            raise ValidationFailure(f"missing required field: {required}")

    try:
        amount = Decimal(str(payload["reported_damage_amount"]))
    except Exception as exc:
        raise ValidationFailure("reported_damage_amount is not a valid decimal") from exc

    sev = severity_mod.compute(amount)

    summary = clean_summary(payload.get("summary"))
    if not summary:
        raise ValidationFailure("summary is empty after sanitization")

    quotes = payload.get("evidence_quotes") or []
    if not isinstance(quotes, list) or not all(isinstance(q, str) for q in quotes):
        raise ValidationFailure("evidence_quotes must be a list of strings")
    if sev in (SeverityLabel.HIGH, SeverityLabel.CRITICAL) and not quotes:
        raise ValidationFailure(
            "HIGH/CRITICAL severity requires at least one evidence_quote from the PDF"
        )
    # Bound the quotes — long ones are unlikely to be verbatim and may hide instructions.
    quotes = tuple(q[:500] for q in quotes[:5])

    try:
        policy_uuid = UUID(str(payload["policy_id"]))
    except (ValueError, TypeError) as exc:
        raise ValidationFailure("policy_id is not a valid UUID") from exc

    return TriageReport(
        pipeline_id=pipeline_id,
        policy_id=policy_uuid,
        pdf_sha256=str(payload["pdf_sha256"]).lower(),
        reported_damage_amount=amount,
        severity=sev,
        summary=summary,
        evidence_quotes=quotes,
    )
