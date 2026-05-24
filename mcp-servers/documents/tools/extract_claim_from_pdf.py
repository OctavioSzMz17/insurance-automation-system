"""Tool: extract_claim_from_pdf — B1.2

Parses an unstructured PDF insurance claim report (Spanish format) into
a structured dict that the analyst_agent can reason over.

Expected PDF format (real-world example):
    REPORTE DE SINIESTRO AUTOMOVILÍSTICO
    Referencia: CASE-2026-0089 | Fecha: 05 de Mayo, 2026
    Asegurado: Juan Pérez (ID: 101)
    Descripción del incidente:
    <free-text incident description across multiple lines>
    Ubicación: <location>
    Daños estimados: <damage summary>

Input schema (enforced by MCP server):
    pdf_path (str, required) — absolute path to the PDF file

Output:
    dict with keys:
        claim_id                (str)         e.g. "CASE-2026-0089"
        user_id                 (int)         policyholder ID
        insured_name            (str)         e.g. "Juan Pérez"
        incident_date           (str)         ISO-8601 date string
        description             (str)         free-text incident summary
        location                (str | None)  optional
        damage_summary          (str | None)  optional qualitative damage description
        reported_damage_amount  (str | None)  optional monetary amount (Decimal as str, Rule 2)

Raises:
    FileNotFoundError — pdf_path does not exist
    ValueError        — PDF is corrupted or required fields are missing

Rules applied:
    Rule 2  — monetary fields serialised as str (Decimal), never float
    Rule 3  — read-only; pure parsing, no DB access
    Rule 9  — Structured logging; no print()
    Rule 11 — Full type hints
"""

import logging
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)

# Fix #84 — resolved once at module load (CLAIM_UPLOAD_DIR is set before the
# server starts and never changes while it is running). Fallback mirrors the
# default in ai-orchestrator/config.py so single-file dev runs still work.
_UPLOAD_DIR: Path = Path(
    os.environ.get("CLAIM_UPLOAD_DIR") or "/tmp/claim-uploads"
).resolve()


# ── Spanish month name → numeric mapping ───────────────────────────
_SPANISH_MONTHS: dict[str, int] = {
    "enero":      1, "febrero":   2, "marzo":     3, "abril":     4,
    "mayo":       5, "junio":     6, "julio":     7, "agosto":    8,
    "septiembre": 9, "setiembre": 9, "octubre":  10, "noviembre": 11,
    "diciembre": 12,
}


def _read_pdf_text(pdf_path: Path) -> str:
    """Open the PDF and return all pages concatenated as text."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        raise ValueError(f"Failed to parse PDF '{pdf_path}': {e}") from e
    return "\n".join(pages)


def _parse_spanish_date(day: str, month_word: str, year: str) -> str:
    """Convert '05 de Mayo 2026' → '2026-05-05'."""
    month_num = _SPANISH_MONTHS.get(month_word.strip().lower())
    if month_num is None:
        raise ValueError(f"Unknown Spanish month: '{month_word}'")
    return f"{int(year):04d}-{month_num:02d}-{int(day):02d}"


def _extract_claim_id(text: str) -> str | None:
    m = re.search(r"Referencia[:\s]+([A-Z0-9][A-Z0-9\-]*)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_insured(text: str) -> tuple[str | None, int | None]:
    """Match 'Asegurado: Juan Pérez (ID: 101)' → ('Juan Pérez', 101)."""
    m = re.search(
        r"Asegurado[:\s]+(.+?)\s*\(ID[:\s]+(\d+)\)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None
    return m.group(1).strip(), int(m.group(2))


def _extract_incident_date(text: str) -> str | None:
    """Match 'Fecha: 05 de Mayo, 2026' → '2026-05-05'."""
    m = re.search(
        r"Fecha[:\s]+(\d{1,2})\s+de\s+(\w+)[,\s]+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return _parse_spanish_date(m.group(1), m.group(2), m.group(3))
    except ValueError as e:
        logger.warning(f"Date parse failed: {e}")
        return None


def _extract_section(text: str, label: str, next_labels: list[str]) -> str | None:
    """Extract a labeled section that may span multiple lines.

    Match starts at `label:` and continues until any of `next_labels:`
    or end of document.
    """
    next_pattern = "|".join(re.escape(n) for n in next_labels)
    # Next label is recognised whether or not it has a colon — handles both
    # labeled sections ("Ubicación:") and bare markers ("Firma del Asegurado").
    pattern = (
        rf"{re.escape(label)}[:\s]+"
        rf"(.+?)"
        rf"(?=\n\s*(?:{next_pattern})|\Z)"
    )
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    cleaned = re.sub(r"\s+", " ", m.group(1)).strip()
    return cleaned or None


def _extract_money(text: str) -> str | None:
    """Find a monetary amount in the text (USD or MXN style).

    Returns Decimal serialised as str, or None if no amount found.
    Examples matched:
        $1,234.56  → "1234.56"
        USD 500    → "500"
        500.00 USD → "500.00"
    """
    patterns = [
        r"\$\s*([\d,]+(?:\.\d{1,2})?)",
        r"USD\s*([\d,]+(?:\.\d{1,2})?)",
        r"([\d,]+(?:\.\d{1,2})?)\s*USD",
        r"MXN\s*([\d,]+(?:\.\d{1,2})?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        try:
            return str(Decimal(raw))
        except InvalidOperation:
            continue
    return None


async def extract_claim_from_pdf(pdf_path: str) -> dict[str, Any]:
    """Extract structured claim data from a Spanish-format PDF claim report.

    Args:
        pdf_path: Absolute path to the PDF file to parse.

    Returns:
        Dict with claim_id, user_id, insured_name, incident_date,
        description, and optional fields (location, damage_summary,
        reported_damage_amount).

    Raises:
        FileNotFoundError: If the PDF file does not exist.
        ValueError: If the PDF is corrupted or claim_id / user_id cannot
                    be extracted (these are mandatory).
    """
    # Fix #84 — enforce path boundary before any file I/O (defense-in-depth).
    # The orchestrator already validated pdf_path, but an attacker holding a
    # leaked X-MCP-Secret could call this tool directly with an arbitrary path.
    # Path.relative_to() raises ValueError when the resolved path escapes
    # _UPLOAD_DIR, which avoids the startswith() prefix-collision edge case.
    path = Path(pdf_path).resolve()
    try:
        path.relative_to(_UPLOAD_DIR)
    except ValueError:
        logger.warning(
            "extract_claim_from_pdf blocked | path outside upload dir | pdf_path=%s",
            pdf_path,
        )
        raise ValueError(
            "pdf_path is outside the permitted upload directory"
        )

    logger.info(f"extract_claim_from_pdf called | pdf_path={pdf_path}")

    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = _read_pdf_text(path)
    if not text.strip():
        raise ValueError(f"PDF '{pdf_path}' contains no extractable text")

    claim_id           = _extract_claim_id(text)
    insured_name, uid  = _extract_insured(text)
    incident_date      = _extract_incident_date(text)

    # Mandatory fields — fail loudly if missing
    if not claim_id:
        raise ValueError(f"No claim_id (Referencia) found in PDF '{pdf_path}'")
    if uid is None:
        raise ValueError(f"No user_id (Asegurado ID) found in PDF '{pdf_path}'")

    # Section extraction — order matters (each section stops at the next label)
    section_labels = ["Ubicación", "Daños estimados", "Firma del Asegurado"]
    description    = _extract_section(text, "Descripción del incidente", section_labels)
    location       = _extract_section(text, "Ubicación",       ["Daños estimados", "Firma del Asegurado"])
    damage_summary = _extract_section(text, "Daños estimados", ["Firma del Asegurado"])

    result: dict[str, Any] = {
        "claim_id":               claim_id,
        "user_id":                uid,
        "insured_name":           insured_name,
        "incident_date":          incident_date,
        "description":            description,
        "location":               location,
        "damage_summary":         damage_summary,
        "reported_damage_amount": _extract_money(text),
    }
    logger.info(
        f"extract_claim_from_pdf result | claim_id={claim_id} user_id={uid} "
        f"date={incident_date}"
    )
    return result
