"""Materialise every case's claim PDF on disk.

Fixtures are generated rather than committed: binary blobs in git cannot be reviewed,
and a payload nobody can read in a diff is a payload nobody audits. The pipeline id for
each case is derived deterministically from the case id, so a given case always maps to
the same file name and a failure is reproducible by hand.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid5

from pypdf import PdfReader

from evals.cases import CASES, Case
from evals.fixtures.pdf_writer import write_pdf

#: Same stable namespace style as scripts/seed/fixtures.py.
_NS = UUID("3f9b1d5e-0000-4000-8000-000000000002")


def pipeline_id_for(case_id: str) -> UUID:
    return uuid5(_NS, f"evals:pipeline:{case_id}")


def build_case_pdf(case: Case, upload_dir: Path) -> Path:
    pipeline_id = pipeline_id_for(case.id)
    return write_pdf(
        upload_dir / f"{pipeline_id}.pdf",
        case.doc.pages(),
        hidden_lines=list(case.doc.hidden) or None,
        metadata=dict(case.doc.metadata) or None,
        truncate_bytes=case.doc.truncate_bytes,
    )


def generate_all(
    upload_dir: Path,
    *,
    cases: tuple[Case, ...] = CASES,
    verbose: bool = False,
) -> dict[str, UUID]:
    """Write one PDF per case and sanity-check each one with pypdf.

    A fixture that silently fails to encode its payload would make the whole case
    meaningless — a green run that proves nothing. So every non-truncated file is parsed
    back and its visible payload confirmed present before the suite runs.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, UUID] = {}

    for case in cases:
        path = build_case_pdf(case, upload_dir)
        pipeline_id = pipeline_id_for(case.id)
        mapping[case.id] = pipeline_id

        if case.doc.truncate_bytes:
            # Expected to be unreadable — that is the point of the fixture.
            if verbose:
                print(f"  {case.id:<32} {path.stat().st_size:>9,} B  (intentionally malformed)")
            continue

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        _assert_payload_present(case, text)
        if verbose:
            print(f"  {case.id:<32} {path.stat().st_size:>9,} B  {len(reader.pages)} page(s)")

    return mapping


def _assert_payload_present(case: Case, text: str) -> None:
    """Fail loudly if a fixture did not actually carry its attack into the text layer."""
    if case.doc.blank:
        if text.strip():
            raise AssertionError(f"{case.id}: blank fixture unexpectedly contains text")
        return

    expected = [case.doc.insured_name, case.doc.amount_text]
    expected += [line for line in case.doc.injection]
    expected += [line for line in case.doc.hidden]
    for needle in expected:
        # The writer drops characters outside Latin-1; compare on what survives.
        survivable = needle.encode("latin-1", errors="replace").decode("latin-1")
        if survivable[:40] not in text:
            raise AssertionError(
                f"{case.id}: fixture is missing its payload — expected {survivable[:40]!r} "
                "in the extracted text"
            )


def main() -> None:
    from evals.harness.workspace import default_upload_dir

    target = default_upload_dir()
    print(f"Generating {len(CASES)} fixture PDFs into {target}")
    generate_all(target, verbose=True)
    print("done")


if __name__ == "__main__":
    main()
