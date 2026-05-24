"""HMAC-SHA256 signing for [trace_id: ...] markers (Master Patch Part 3).

Shared by:
    - ai-orchestrator/routes/claims.py        — signs every marker before
                                                 it is written to the DB.
    - mcp-servers/claims/tools/append_reasoning.py — rejects any caller-
      controlled text containing unsigned markers, closing the spoofing
      vector identified in the post-Fix-1 audit (Medium #2).

Why a shared utility:
    Both the orchestrator (signer) and the claims MCP (verifier on writes)
    must use the same canonical format and the same secret. Living in
    packages/common ensures bit-identical behaviour and a single source
    of truth.

Wire format:
    [trace_id: <trace_id>.<base64url(hmac-sha256(secret, trace_id)[:12])>]

    The 96-bit truncated HMAC defeats brute-force at conversational rates
    (10^28 attempts to forge a marker for a chosen trace_id). The full
    32-byte HMAC would make markers ugly and oversize the column.

Rules applied:
    Rule 7  — Audit trail tamper-evident
    Rule 8  — Secret loaded from env, never hardcoded
    Rule 11 — Strict type hints throughout
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
import re
from hashlib import sha256
from typing import Optional

logger = logging.getLogger(__name__)

# Marker syntax — whitespace tolerant, case insensitive on the literal.
# The captured group is the inner VALUE (trace_id.signature).
_MARKER_RE: re.Pattern[str] = re.compile(
    r"\[\s*trace_id\s*:\s*([A-Za-z0-9._\-]+)\s*\]",
    re.IGNORECASE,
)

# 96-bit truncated HMAC. Trades length for column-width without affecting
# security at any realistic forge attempt rate.
_SIG_BYTES: int = 12

# Minimum entropy for the signing key — same threshold as MCP_SHARED_SECRET.
_MIN_KEY_BYTES: int = 32


def _load_secret() -> bytes:
    """Lazy load of PROPOSAL_SIGNING_KEY from process env.

    Lazy on purpose: MCP servers that never touch trace markers
    (users, policies, documents, legacy) must not be forced to set
    the key. The orchestrator and claims MCP fail fast on first
    sign/verify attempt if the key is absent.
    """
    raw = os.environ.get("PROPOSAL_SIGNING_KEY", "")
    if not raw or len(raw) < _MIN_KEY_BYTES:
        raise RuntimeError(
            "PROPOSAL_SIGNING_KEY must be set in .env and at least "
            f"{_MIN_KEY_BYTES} characters long. "
            "Generate one with: openssl rand -hex 32"
        )
    return raw.encode("utf-8")


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def sign_trace_marker(trace_id: str) -> str:
    """Build a signed marker literal: `[trace_id: <tid>.<sig>]`.

    The trace_id must not contain '.' (used as the field separator).
    A typical input is a UUIDv4 string, which never contains '.'.
    """
    if not trace_id:
        raise ValueError("trace_id must be non-empty")
    if "." in trace_id:
        raise ValueError("trace_id must not contain '.'")
    secret = _load_secret()
    digest = hmac.new(secret, trace_id.encode("utf-8"), sha256).digest()[:_SIG_BYTES]
    return f"[trace_id: {trace_id}.{_b64url_no_pad(digest)}]"


def verify_trace_marker_value(value: str) -> Optional[str]:
    """Given the inner VALUE of `[trace_id: VALUE]`, return the trace_id
    iff its signature is valid, else None.

    Constant-time compare on the HMAC bytes — immune to timing oracle.
    """
    if not value or "." not in value:
        return None
    tid, sig_b64 = value.rsplit(".", 1)
    if not tid or not sig_b64:
        return None
    try:
        presented = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return None
    if len(presented) != _SIG_BYTES:
        return None
    secret = _load_secret()
    expected = hmac.new(secret, tid.encode("utf-8"), sha256).digest()[:_SIG_BYTES]
    if not hmac.compare_digest(presented, expected):
        return None
    return tid


def extract_verified_trace_ids(text: Optional[str]) -> list[str]:
    """Return every verified trace_id present in `text`, chronological order.

    Unsigned or invalid markers are silently dropped — the caller never
    sees a trace_id whose authenticity cannot be proven.
    """
    if not text:
        return []
    out: list[str] = []
    for m in _MARKER_RE.finditer(text):
        tid = verify_trace_marker_value(m.group(1))
        if tid is not None:
            out.append(tid)
    return out


def text_contains_unsigned_trace_markers(text: Optional[str]) -> bool:
    """True iff `text` contains any `[trace_id: ...]` marker whose
    signature does NOT verify under the current secret.

    Used by mcp-servers/claims/tools/append_reasoning.py to reject
    caller-controlled text that attempts to forge audit-trail markers.
    """
    if not text:
        return False
    for m in _MARKER_RE.finditer(text):
        if verify_trace_marker_value(m.group(1)) is None:
            logger.warning(
                "unsigned/invalid trace_id marker detected in candidate text"
            )
            return True
    return False
