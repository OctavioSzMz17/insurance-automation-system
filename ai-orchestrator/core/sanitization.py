"""Server-side sanitization of LLM-emitted text fields (Rule 4).

The contract: any free-text field that may reach UI must pass through `clean_summary`
before being placed into a Pydantic model. This is a defense in depth against:
  - Control characters that could break terminals or logs
  - ANSI escape sequences (`\\x1b[...`) that could rewrite a log viewer
  - HTML/XML that could render as markup downstream
  - Excessive length (caps at 200 chars per Rule 4)
"""
from __future__ import annotations

import html
import re
from typing import Final

_MAX_LEN: Final[int] = 200

# Strip C0 control chars except whitespace we keep (newline/tab/space), plus DEL.
_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# CSI sequences: ESC [ ... letter
_ANSI_CSI_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Other ESC sequences (OSC, simpler patterns)
_ANSI_OTHER_RE: Final[re.Pattern[str]] = re.compile(r"\x1b[@-Z\\-_]")


def clean_summary(text: str | None) -> str:
    """Return a UI-safe, length-bounded version of an LLM summary string.

    Empty / None input returns an empty string — the caller is responsible for treating
    that as a validation failure if a summary is required.
    """
    if not text:
        return ""
    s = str(text)
    # 1. Strip ANSI escape sequences BEFORE control-char strip so the ESC bytes go too.
    s = _ANSI_CSI_RE.sub("", s)
    s = _ANSI_OTHER_RE.sub("", s)
    # 2. Strip remaining control chars.
    s = _CONTROL_RE.sub("", s)
    # 3. Collapse internal whitespace runs (incl. newlines) to a single space.
    s = re.sub(r"\s+", " ", s).strip()
    # 4. HTML-escape so any < > & " ' that survive render as text, not markup.
    s = html.escape(s, quote=True)
    # 5. Hard cap at 200 chars. Truncation is silent — the model is told to keep it short.
    if len(s) > _MAX_LEN:
        s = s[:_MAX_LEN]
    return s
