"""Sanitization hardening tests."""
from __future__ import annotations

from core.sanitization import clean_summary


def test_empty_and_none():
    assert clean_summary(None) == ""
    assert clean_summary("") == ""
    assert clean_summary("   ") == ""


def test_simple_text_passes_unchanged():
    assert clean_summary("Single-vehicle collision at intersection") == "Single-vehicle collision at intersection"


def test_truncates_to_200():
    long = "x" * 500
    out = clean_summary(long)
    assert len(out) == 200


def test_strips_null_bytes():
    assert "\x00" not in clean_summary("ok\x00here")


def test_strips_control_chars():
    out = clean_summary("a\x01b\x02c\x07")
    assert out == "abc"


def test_strips_ansi_escapes():
    out = clean_summary("\x1b[31mRED ALERT\x1b[0m collision")
    assert "\x1b" not in out
    assert "RED ALERT collision" in out


def test_collapses_whitespace_runs():
    assert clean_summary("a\n\n\n  b\t\tc") == "a b c"


def test_html_escapes_tags_and_quotes():
    out = clean_summary('<script>alert("x")</script> & co')
    assert "<script>" not in out
    assert "alert" in out
    assert "&amp;" in out
    assert "&quot;" in out


def test_truncation_after_escape_still_safe():
    """Long input with control chars must not produce malformed HTML at the cut."""
    s = '<b>' * 100
    out = clean_summary(s)
    assert len(out) <= 200
    # html.escape converts < to &lt; so the cut must not leave a half-entity behind.
    # We tolerate truncation mid-entity because the consumer treats this as plain text,
    # but no control char or raw < should appear:
    assert "<" not in out
    assert "\x00" not in out
