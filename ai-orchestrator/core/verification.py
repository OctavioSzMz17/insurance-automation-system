"""Deterministic claim verification — the hard facts the LLM is not allowed to decide (Rule 4).

These three checks are the counterweight to the agents: whatever the analyst says, the
system independently establishes (a) whether the policy is actually in force, (b) whether
the document really names the claimant, and (c) whether the reported figure really appears
in the document. The results are fed to the auditor as GROUND TRUTH and surfaced as review
flags in the HITL screen.

Every function here is PURE — no I/O, no DB, no LLM, no FastAPI. That is deliberate: it
keeps the security-critical logic unit-testable and lets the adversarial eval suite
(`evals/`) exercise it directly without standing up the HTTP stack.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

__all__ = [
    "evaluate_coverage",
    "evaluate_identity",
    "evaluate_amount",
    "evaluate_policy_ownership",
    "build_review_flags",
    "extract_monetary_values",
    "normalize_text",
    "unverified",
]


def unverified(reason: str) -> dict[str, Any]:
    """A check that was required but could not be completed.

    This exists because the dangerous failure mode of a verification layer is not a
    check that says "no" — it is a check that says nothing. A `None` block is
    indistinguishable from a clean result, so any caller that *attempted* a check and
    failed must record that explicitly. `build_review_flags` turns these into review
    flags, which makes the layer fail closed: an MCP timeout raises a flag instead of
    silently waving the claim through.
    """
    return {"verified": False, "reason": reason}


def evaluate_coverage(policy: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Deterministic policy-validity check (Rule 4: hard facts are not left to the LLM).

    A policy is in force when its status is ACTIVE *and* today falls within the
    [effective_from, effective_to] window. ISO YYYY-MM-DD strings compare lexically.

    `today` is injectable so tests and evals are not calendar-dependent.
    """
    status = str(policy.get("status") or "").upper()
    ef = str(policy.get("effective_from") or "")[:10]
    et = str(policy.get("effective_to") or "")[:10]
    now = (today or date.today()).isoformat()

    active = status == "ACTIVE"
    in_window = bool(ef and et and ef <= now <= et)
    in_force = active and in_window

    if in_force:
        reason = "Póliza vigente."
    elif not active:
        reason = f"Estado de póliza: {status or 'desconocido'}."
    elif et and now > et:
        reason = f"La vigencia terminó el {et}."
    elif ef and now < ef:
        reason = f"La vigencia inicia el {ef}."
    else:
        reason = "Vigencia no determinable."

    return {
        "in_force": in_force,
        "status": status,
        "effective_from": ef or None,
        "effective_to": et or None,
        "reason": reason,
    }


def normalize_text(s: str) -> str:
    """Lowercase + strip accents + collapse whitespace, for robust name matching."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def evaluate_identity(claimant_name: str | None, pdf_text: str) -> dict[str, Any]:
    """Deterministic anti-impersonation check (Rule 4).

    Confirms the claim document actually names the CLIENT the operator filed it
    for. Catches "filed on behalf of Ana but the PDF belongs to Carlos".
    A match is the full normalized name as a substring, OR every name token
    (len>=3) present as a whole word.
    """
    name = normalize_text(claimant_name or "")
    text = normalize_text(pdf_text or "")
    if not name:
        # No name on record means the check cannot be performed — not that it passed.
        return unverified("El cliente seleccionado no tiene nombre registrado.")

    tokens = [t for t in name.split() if len(t) >= 3]
    tokens_present = bool(tokens) and all(re.search(rf"\b{re.escape(t)}\b", text) for t in tokens)
    match = (name in text) or tokens_present

    reason = (
        "El documento menciona al asegurado seleccionado."
        if match else
        f"El documento NO menciona a «{claimant_name}» — posible suplantación de identidad."
    )
    return {"match": match, "claimant_name": claimant_name, "reason": reason}


def evaluate_amount(reported_amount: Any, pdf_text: str) -> dict[str, Any]:
    """Deterministic anti-hallucination check on the monetary amount (Rule 4).

    An LLM may invent a damage figure not present in the document. This confirms
    the reported amount actually appears in the PDF text. $0 (no amount claimed)
    is treated as supported — it is the honest 'no figure stated' outcome.
    """
    if reported_amount in (None, ""):
        return unverified("Sin monto reportado.")
    try:
        val = round(float(reported_amount), 2)
    except (TypeError, ValueError):
        return unverified(f"Monto no numérico: {reported_amount!r}.")
    if val == 0:
        return {"supported": True, "amount": reported_amount, "reason": "Sin monto reclamado ($0)."}

    supported = val in extract_monetary_values(pdf_text)
    reason = (
        "El monto reportado aparece en el documento."
        if supported else
        f"El monto reportado (${reported_amount}) NO aparece en el documento — posible valor inventado por la IA."
    )
    return {"supported": supported, "amount": reported_amount, "reason": reason}


def extract_monetary_values(pdf_text: str) -> set[float]:
    """Collect every monetary-looking number present in the document text.

    Thousands separators are stripped so `10,000.00` matches `10000.0`. Values are
    rounded to 2 decimals to match the NUMERIC(14,2) precision used end to end.
    """
    values: set[float] = set()
    for tok in re.findall(r"\$?\s*(\d[\d.,]*)", pdf_text or ""):
        cleaned = tok.replace(",", "").rstrip(".")
        try:
            values.add(round(float(cleaned), 2))
        except ValueError:
            continue
    return values


def evaluate_policy_ownership(
    policy: dict[str, Any],
    *,
    claimant_client_id: str | None,
) -> dict[str, Any]:
    """Deterministic cross-tenant check: does this policy belong to the claimant?

    The policy ACL answers "may the *caller* read this policy", which is a different
    question. A staff operator is entitled to read every policy, so nothing stops a
    claim filed for one client from being attributed to another client's policy — the
    coverage, identity and amount checks would all pass while the payout is booked
    against a stranger's coverage limit.

    `policy["user_id"]` is the owning client's id, as the policies repository aliases it.
    """
    owner_id = str(policy.get("user_id") or "")
    if not claimant_client_id:
        return unverified("No se pudo resolver el cliente del siniestro.")
    if not owner_id:
        return unverified("La póliza no expone su cliente propietario.")

    belongs = owner_id == str(claimant_client_id)
    reason = (
        "La póliza pertenece al asegurado del siniestro."
        if belongs else
        "La póliza NO pertenece al asegurado del siniestro — el siniestro se imputaría "
        "a la cobertura de otro cliente."
    )
    return {"belongs": belongs, "policy_owner_id": owner_id,
            "claimant_client_id": str(claimant_client_id), "reason": reason}


# Each entry is (block name, the key holding the verdict, how to phrase the flag).
# Adding a deterministic check means adding one row here plus its evaluate_* function.
_FLAG_SPECS: tuple[tuple[str, str, Any], ...] = (
    ("coverage", "in_force", lambda b: (
        f"Póliza no vigente: {b.get('reason')} "
        f"(vigencia {b.get('effective_from')} → {b.get('effective_to')})."
    )),
    ("ownership", "belongs", lambda b: f"Póliza ajena: {b.get('reason')}"),
    ("identity", "match", lambda b: f"Posible suplantación: {b.get('reason')}"),
    ("amount", "supported", lambda b: f"Monto no respaldado: {b.get('reason')}"),
)


def build_review_flags(
    *,
    coverage: dict[str, Any] | None = None,
    ownership: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    amount: dict[str, Any] | None = None,
) -> list[str]:
    """Turn the verification report into human-readable review flags.

    A non-empty list means at least one deterministic red flag fired. The auditor
    receives these as ground truth, the HITL screen renders them, and the mutation
    handler refuses to persist an APPROVED decision while any of them stands.

    Three distinct states per check, and the middle one is the one that matters:
      * `None`              — not applicable to this claim; contributes nothing.
      * `verified is False` — attempted and inconclusive; raises a flag (fail closed).
      * verdict is False    — attempted and negative; raises the specific flag.
    """
    blocks = {"coverage": coverage, "ownership": ownership, "identity": identity, "amount": amount}
    flags: list[str] = []
    for name, verdict_key, render in _FLAG_SPECS:
        block = blocks.get(name)
        if block is None:
            continue
        if block.get("verified") is False:
            flags.append(f"Verificación incompleta ({name}): {block.get('reason')}")
            continue
        if block.get(verdict_key) is False:
            flags.append(render(block))
    return flags
