"""Redaction applied before anything leaves the process (Rule 8 / Rule 9).

Two separate concerns, deliberately not conflated:

**Volume.** Document text must never travel in a span. A claim PDF is tens of thousands
of characters; a span carrying it is useless in a UI, expensive in a trace store, and
fatal over SSE through a proxy. Callers summarise it with `summarize_text` instead — the
instrumentation never passes raw document text, at any masking level.

**Privacy.** Spans describe real insured people. Emails, names and addresses are redacted
at `pii` level and above, so a trace is still navigable — a stable pseudonym keeps
"same claimant across these three traces" answerable — without carrying the identity.

Level comes from `OBSERVABILITY_MASK`, defaulting to `none` in development (where traces
are only useful if you can read them) and `pii` everywhere else. Production never has to
remember to turn it on.
"""
from __future__ import annotations

import hashlib
import os
import re
from enum import StrEnum
from typing import Any, Final

_MAX_STRING = 500
_MAX_ITEMS = 50

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

#: Keys whose values are personal data regardless of what they contain.
_PII_KEYS: Final[frozenset[str]] = frozenset({
    "email", "full_name", "claimant_name", "name", "address", "phone",
    "claimant_sub", "submitted_by_sub", "submitter_sub", "owner_email",
    "approver_sub", "caller_sub", "sub", "user",
})

#: Keys that would carry document or prompt bodies. Never emitted verbatim.
_BULK_KEYS: Final[frozenset[str]] = frozenset({
    "text", "pdf_text", "content", "messages", "system", "prompt", "evidence_quotes",
})

_PROTECTED_ENVS: Final[frozenset[str]] = frozenset({"production", "prod", "staging"})


class MaskLevel(StrEnum):
    NONE = "none"      # emit as-is; local development only
    PII = "pii"        # pseudonymise identities, summarise bulk fields
    STRICT = "strict"  # drop identities entirely


def default_level(environment: str | None = None) -> MaskLevel:
    explicit = os.environ.get("OBSERVABILITY_MASK", "").strip().lower()
    if explicit:
        try:
            return MaskLevel(explicit)
        except ValueError:
            pass
    env = (environment or os.environ.get("ENVIRONMENT", "development")).lower()
    return MaskLevel.PII if env in _PROTECTED_ENVS else MaskLevel.NONE


def pseudonym(value: str) -> str:
    """A stable, non-reversible handle for an identity.

    Stable so that "the same claimant appears in these traces" stays answerable;
    truncated so it cannot be brute-forced back into an email with a rainbow table of
    one domain's users (and it is not a security boundary either way — it is a courtesy
    to anyone reading a trace, not a substitute for access control on the trace store).
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"anon:{digest[:10]}"


def summarize_text(text: str | None) -> dict[str, Any]:
    """What instrumentation sends instead of a document body."""
    if not text:
        return {"chars": 0, "sha256_12": None}
    return {
        "chars": len(text),
        "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }


def build_masker(level: MaskLevel | None = None):
    """Return a `mask(value) -> value` callable for the given level."""
    effective = level or default_level()

    def mask(value: Any, *, _key: str | None = None, _depth: int = 0) -> Any:
        if effective is MaskLevel.NONE:
            return _bound(value, _depth)
        return _mask_value(value, effective, _key, _depth)

    return mask


def _bound(value: Any, depth: int) -> Any:
    """Even unmasked, nothing unbounded gets emitted."""
    if depth > 6:
        return "<…>"
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + f"…(+{len(value) - _MAX_STRING})"
    if isinstance(value, dict):
        return {k: _bound(v, depth + 1) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [_bound(v, depth + 1) for v in list(value)[:_MAX_ITEMS]]
    return value


def _mask_value(value: Any, level: MaskLevel, key: str | None, depth: int) -> Any:
    if depth > 6:
        return "<…>"

    if isinstance(value, dict):
        return {
            k: _mask_value(v, level, str(k).lower(), depth + 1)
            for k, v in list(value.items())[:_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_mask_value(v, level, key, depth + 1) for v in list(value)[:_MAX_ITEMS]]

    if isinstance(value, str):
        if key in _BULK_KEYS:
            return summarize_text(value)
        if key in _PII_KEYS:
            return "<redacted>" if level is MaskLevel.STRICT else pseudonym(value)
        redacted = _EMAIL_RE.sub(
            lambda m: "<redacted>" if level is MaskLevel.STRICT else pseudonym(m.group(0)),
            value,
        )
        return _bound(redacted, depth)

    if key in _BULK_KEYS and isinstance(value, (dict, list)):
        return {"items": len(value)}
    return value


__all__ = ["MaskLevel", "build_masker", "default_level", "pseudonym", "summarize_text"]
