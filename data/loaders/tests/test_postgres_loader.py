"""Unit tests for postgres_loader.py (A2.2) — A4.1

All tests are pure unit tests — no DB, no .env required.
check_validation_report uses tmp_path to create real temporary JSON files.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.loaders.postgres_loader import _parse_policy_id, check_validation_report


# ── _parse_policy_id ───────────────────────────────────────────────

class TestParsePolicyId:
    def test_pol_prefix(self) -> None:
        assert _parse_policy_id("POL-001") == 1

    def test_pol_prefix_no_leading_zero(self) -> None:
        assert _parse_policy_id("POL-042") == 42

    def test_plain_integer_string(self) -> None:
        assert _parse_policy_id("7") == 7

    def test_policy_prefix(self) -> None:
        assert _parse_policy_id("POLICY-100") == 100

    def test_p_prefix(self) -> None:
        assert _parse_policy_id("P-003") == 3

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract numeric ID"):
            _parse_policy_id("INVALID")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_policy_id("")

    def test_only_letters_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_policy_id("POL-ABC")


# ── check_validation_report ────────────────────────────────────────

class TestCheckValidationReport:
    def _write_report(self, tmp_path: Path, status: str, invalid: int = 0) -> str:
        report = {
            "status": status,
            "total_rows_valid": 10 - invalid,
            "total_rows_invalid": invalid,
        }
        path = tmp_path / "validation_report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def test_valid_report_passes(self, tmp_path: Path) -> None:
        path = self._write_report(tmp_path, "VALID")
        # Should not raise
        check_validation_report(path)

    def test_invalid_report_raises_value_error(self, tmp_path: Path) -> None:
        path = self._write_report(tmp_path, "INVALID", invalid=3)
        with pytest.raises(ValueError, match="INVALID"):
            check_validation_report(path)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError):
            check_validation_report(missing)

    def test_wrong_status_string_raises(self, tmp_path: Path) -> None:
        path = self._write_report(tmp_path, "PENDING")
        with pytest.raises(ValueError):
            check_validation_report(path)
