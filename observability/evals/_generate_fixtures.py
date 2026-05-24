"""One-off generator for the golden-set PDFs — Phase D6.1.

Why a script and not the PDFs by hand:
    - Reproducibility. Anyone can regenerate the fixtures from source.
    - When we need a new case, copy the PATTERN here, change the text.
    - PDFs are still committed (the eval runner consumes them) — this
      script just documents how they were produced.

Run once inside the orchestrator container (it already has Python +
reportlab will be installed by this script):

    docker exec --user root insureai-orchestrator \\
        pip install --quiet reportlab
    docker exec insureai-orchestrator \\
        python -m observability.evals._generate_fixtures
    docker cp \\
        insureai-orchestrator:/app/observability/evals/golden \\
        observability/evals/

The PDFs are intentionally small (~2-3 KB each); committing them is
fine and makes the eval harness self-contained.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_HERE = Path(__file__).resolve().parent
_GOLDEN = _HERE / "golden"
logger = logging.getLogger("observability.evals.generate_fixtures")


# ── Case fixtures ────────────────────────────────────────────────

CASES = [
    {
        "dir": "case_001_clean_approval",
        "data": {
            "reference":          "EVAL-001",
            "date":               "12 de Mayo, 2026",
            "user_id":            103,
            "user_name":          "Carlos Rodríguez",
            "incident_date_text": "07 de Mayo de 2026",
            "description": (
                "El día 07 de Mayo de 2026, aproximadamente a las 18:40 horas, "
                "circulaba por la Avenida Juárez en dirección poniente a "
                "velocidad moderada cuando, al detenerme en el semáforo de la "
                "intersección con Calle Hidalgo, el vehículo de atrás no "
                "alcanzó a frenar a tiempo y golpeó la parte trasera de mi "
                "automóvil. El conductor responsable se identificó, "
                "intercambiamos datos del seguro y se levantó parte oficial "
                "con el agente de tránsito (folio TR-22481). No hubo personas "
                "lesionadas. Mi vehículo presenta abolladuras en la defensa "
                "trasera y daños menores en la cajuela; el resto del auto se "
                "encuentra en buen estado y es operable."
            ),
            "location": "Av. Juárez esquina Calle Hidalgo, Pachuca, Hidalgo",
            "damages": (
                "Abolladuras en defensa trasera, daño en cajuela y rayones "
                "superficiales en pintura. Sin afectación mecánica."
            ),
            "estimated_amount": "$ 4,200.00 USD",
        },
    },
    {
        "dir": "case_002_outside_vigencia",
        "data": {
            "reference":          "EVAL-002",
            "date":               "03 de Abril, 2026",
            "user_id":            102,
            "user_name":          "María García",
            "incident_date_text": "01 de Abril de 2026",
            "description": (
                "El día 01 de Abril de 2026, mientras me incorporaba al "
                "carril central del Boulevard Reforma a las 11:20 horas, fui "
                "embestida por otro vehículo que circulaba a exceso de "
                "velocidad. El otro conductor se dio a la fuga. Mi automóvil "
                "sufrió daños considerables en el costado izquierdo y la "
                "puerta del conductor. Levanté denuncia ante el ministerio "
                "público (folio MP-3145/2026)."
            ),
            "location": "Boulevard Reforma km 8.5, Pachuca, Hidalgo",
            "damages": (
                "Daños en costado izquierdo, puerta del conductor abollada "
                "y rota, espejo lateral destruido, rayones extensos."
            ),
            "estimated_amount": "$ 6,800.00 USD",
        },
    },
]


# ── Renderer ─────────────────────────────────────────────────────

def _build_pdf(out_path: Path, data: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=letter,
        rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=72,
    )
    styles = getSampleStyleSheet()
    title  = ParagraphStyle("Title", parent=styles["Heading1"],
                            fontSize=14, alignment=1, spaceAfter=12)
    head   = ParagraphStyle("Head", parent=styles["Normal"],
                            fontSize=10, alignment=1, spaceAfter=18)
    label  = ParagraphStyle("Label", parent=styles["Normal"],
                            fontSize=10, fontName="Helvetica-Bold", spaceAfter=6)
    body   = ParagraphStyle("Body", parent=styles["Normal"],
                            fontSize=10, leading=14, spaceAfter=12)

    story = [
        Paragraph("REPORTE DE SINIESTRO AUTOMOVILÍSTICO", title),
        Paragraph(
            f"Referencia: {data['reference']} | Fecha del reporte: {data['date']}",
            head,
        ),
        Paragraph(
            f"Asegurado: {data['user_name']} (ID: {data['user_id']})", label,
        ),
        Paragraph(f"Fecha del incidente: {data['incident_date_text']}", label),
        Spacer(1, 0.15 * inch),
        Paragraph("Descripción del incidente:", label),
        Paragraph(data["description"], body),
        Paragraph(f"<b>Ubicación:</b> {data['location']}", body),
        Paragraph(f"<b>Daños estimados:</b> {data['damages']}", body),
        Paragraph(
            f"<b>Monto estimado de reparación:</b> {data['estimated_amount']}",
            body,
        ),
        Spacer(1, 0.4 * inch),
        Paragraph("Firma del Asegurado", body),
    ]
    doc.build(story)


def main() -> None:
    for case in CASES:
        out = _GOLDEN / case["dir"] / "claim.pdf"
        _build_pdf(out, case["data"])
        logger.info("Fixture generated | path=%s", out)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    main()
