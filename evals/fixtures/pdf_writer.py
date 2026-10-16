"""Minimal, dependency-free PDF writer used to build eval fixtures.

Why hand-rolled instead of reportlab: the adversarial fixtures need capabilities a
high-level library makes awkward or impossible — white-on-white text, payloads placed
only in the Info dictionary, deliberately truncated files, and multi-megabyte filler.
Writing the handful of PDF objects directly is ~120 lines, adds no dependency to the
project, and every output is validated against pypdf by `generate.py`.

Scope: PDF 1.4, single Type1 font (Helvetica), WinAnsi text. That is all a claim
document fixture needs. This is test-fixture tooling — it is never imported by the
application.
"""
from __future__ import annotations

from pathlib import Path

_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN_X = 56
_TOP_Y = 736


def _escape(text: str) -> str:
    """Escape the three characters that are special inside a PDF literal string."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _encode(text: str) -> bytes:
    """PDF literal strings here use WinAnsi/Latin-1; unmappable chars are dropped.

    Fixtures that need characters outside Latin-1 put them in the Info dictionary
    (UTF-16BE) instead — see `generate.py`.
    """
    return text.encode("latin-1", errors="replace")


def _text_block(lines: list[str], *, start_y: int, font_size: int, white: bool) -> str:
    """One BT/ET text block. `white` paints in white ink (invisible on white paper)."""
    leading = font_size + 4
    out = ["BT"]
    if white:
        out.append("1 1 1 rg")
    out += [f"/F1 {font_size} Tf", f"{leading} TL", f"1 0 0 1 {_MARGIN_X} {start_y} Tm"]
    for line in lines:
        out.append(f"({_escape(line)}) Tj")
        out.append("T*")
    out.append("ET")
    return "\n".join(out)


def _utf16_string(text: str) -> bytes:
    """A PDF text string in UTF-16BE with the mandatory byte-order mark."""
    raw = b"\xfe\xff" + text.encode("utf-16-be")
    escaped = bytearray()
    for byte in raw:
        if byte in (0x28, 0x29, 0x5C):  # ( ) \
            escaped += b"\\"
        escaped.append(byte)
    return bytes(escaped)


def write_pdf(
    path: Path,
    pages: list[list[str]],
    *,
    hidden_lines: list[str] | None = None,
    metadata: dict[str, str] | None = None,
    font_size: int = 11,
    truncate_bytes: int = 0,
) -> Path:
    """Write a PDF whose pages contain the given lines of text.

    Args:
        path: destination file.
        pages: one list of text lines per page.
        hidden_lines: painted in white ink on page 1 — invisible to a human reader,
            fully extractable by a text parser. Models the classic "hidden prompt"
            attack against document-ingesting agents.
        metadata: /Info dictionary entries (e.g. Title, Author, Keywords), written
            as UTF-16BE so non-Latin-1 payloads can be represented.
        font_size: base text size in points.
        truncate_bytes: when > 0, chop this many bytes off the end of the file to
            produce a structurally invalid PDF (robustness fixture).

    Returns:
        The path written.
    """
    if not pages:
        raise ValueError("a PDF needs at least one page")

    # Object numbering: 1 catalog, 2 pages, 3 font, 4 info, then (page, contents) pairs.
    first_page_obj = 5
    page_obj_nums = [first_page_obj + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }

    info_parts = bytearray(b"<<")
    for key, value in (metadata or {}).items():
        info_parts += b" /" + key.encode("ascii") + b" (" + _utf16_string(value) + b")"
    info_parts += b" >>"
    objects[4] = bytes(info_parts)

    for index, lines in enumerate(pages):
        page_num = page_obj_nums[index]
        contents_num = page_num + 1
        stream = _text_block(lines, start_y=_TOP_Y, font_size=font_size, white=False)
        if index == 0 and hidden_lines:
            stream += "\n" + _text_block(
                hidden_lines, start_y=_TOP_Y - 400, font_size=font_size, white=True
            )
        body = _encode(stream)
        objects[page_num] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Contents {contents_num} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode("ascii")
        objects[contents_num] = (
            f"<< /Length {len(body)} >>\nstream\n".encode("ascii") + body + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("ascii") + objects[num] + b"\nendobj\n"

    xref_offset = len(out)
    highest = max(objects)
    out += f"xref\n0 {highest + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in range(1, highest + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {highest + 1} /Root 1 0 R /Info 4 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    payload = bytes(out)
    if truncate_bytes > 0:
        payload = payload[:-truncate_bytes]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path
