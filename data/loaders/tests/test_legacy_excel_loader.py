"""Unit tests for legacy_excel_loader.py (A2.1) — A4.1

All tests are pure unit tests — no Excel file, no DB, no .env required.
DataFrames are built in-memory using pandas.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.loaders.legacy_excel_loader import (
    _safe_decimal,
    _safe_date,
    validate_users,
    validate_policies,
)
from packages.common.enums import PolicyStatus


# ── _safe_decimal ──────────────────────────────────────────────────

class TestSafeDecimal:
    def test_integer_string(self) -> None:
        assert _safe_decimal("500", "field") == Decimal("500")

    def test_float_string(self) -> None:
        assert _safe_decimal("1234.56", "field") == Decimal("1234.56")

    def test_integer_value(self) -> None:
        assert _safe_decimal(100, "field") == Decimal("100")

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot convert"):
            _safe_decimal("not-a-number", "deductible_usd")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError):
            _safe_decimal(None, "field")


# ── _safe_date ─────────────────────────────────────────────────────

class TestSafeDate:
    def test_date_passthrough(self) -> None:
        d = date(2025, 1, 15)
        assert _safe_date(d, "start_date") == d

    def test_iso_string(self) -> None:
        assert _safe_date("2025-01-15", "start_date") == date(2025, 1, 15)

    def test_timestamp(self) -> None:
        ts = pd.Timestamp("2025-06-01")
        assert _safe_date(ts, "start_date") == date(2025, 6, 1)

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            _safe_date("15/01/2025", "start_date")

    def test_nonsense_raises(self) -> None:
        with pytest.raises(ValueError):
            _safe_date("not-a-date", "start_date")


# ── validate_users ─────────────────────────────────────────────────

class TestValidateUsers:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_valid_users_returned(self) -> None:
        df = self._make_df([
            {"user_id": 101, "name": "Juan Pérez", "email": "juan@test.com"},
            {"user_id": 102, "name": "Ana López",  "email": "ana@test.com"},
        ])
        valid, errors = validate_users(df, "Users")
        assert len(valid) == 2
        assert len(errors) == 0
        assert valid[0].user_id == 101
        assert valid[1].email == "ana@test.com"

    def test_duplicate_email_caught(self) -> None:
        df = self._make_df([
            {"user_id": 101, "name": "Juan", "email": "same@test.com"},
            {"user_id": 102, "name": "Ana",  "email": "same@test.com"},
        ])
        valid, errors = validate_users(df, "Users")
        assert len(valid) == 1
        assert len(errors) == 1
        assert "Duplicate email" in errors[0].error

    def test_invalid_email_caught(self) -> None:
        df = self._make_df([
            {"user_id": 101, "name": "Juan", "email": "not-an-email"},
        ])
        valid, errors = validate_users(df, "Users")
        assert len(valid) == 0
        assert len(errors) == 1

    def test_empty_dataframe(self) -> None:
        df = self._make_df([])
        valid, errors = validate_users(df, "Users")
        assert valid == []
        assert errors == []


# ── validate_policies ──────────────────────────────────────────────

class TestValidatePolicies:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def _valid_row(self, user_id: int = 101) -> dict:
        return {
            "policy_id":          "POL-001",
            "user_id":            user_id,
            "type":               "Premium Full Coverage",
            "status":             "ACTIVE",
            "deductible_usd":     "500.00",
            "coverage_limit_usd": "50000.00",
            "start_date":         "2025-01-01",
            "end_date":           "2026-01-01",
        }

    def test_valid_policy_returned(self) -> None:
        df = self._make_df([self._valid_row()])
        valid, errors = validate_policies(df, "Policies", {101})
        assert len(valid) == 1
        assert len(errors) == 0
        assert valid[0].deductible_usd == Decimal("500.00")
        assert valid[0].status == PolicyStatus.ACTIVE

    def test_fk_violation_caught(self) -> None:
        df = self._make_df([self._valid_row(user_id=999)])
        valid, errors = validate_policies(df, "Policies", {101})
        assert len(valid) == 0
        assert len(errors) == 1
        assert "FK violation" in errors[0].error

    def test_deductible_exceeds_coverage_caught(self) -> None:
        row = self._valid_row()
        row["deductible_usd"]     = "60000.00"
        row["coverage_limit_usd"] = "50000.00"
        df = self._make_df([row])
        valid, errors = validate_policies(df, "Policies", {101})
        assert len(valid) == 0
        assert len(errors) == 1

    def test_end_before_start_caught(self) -> None:
        row = self._valid_row()
        row["start_date"] = "2026-01-01"
        row["end_date"]   = "2025-01-01"
        df = self._make_df([row])
        valid, errors = validate_policies(df, "Policies", {101})
        assert len(valid) == 0
        assert len(errors) == 1

    def test_invalid_status_caught(self) -> None:
        row = self._valid_row()
        row["status"] = "UNKNOWN_STATUS"
        df = self._make_df([row])
        valid, errors = validate_policies(df, "Policies", {101})
        assert len(valid) == 0
        assert len(errors) == 1

    def test_empty_dataframe(self) -> None:
        df = self._make_df([])
        valid, errors = validate_policies(df, "Policies", {101})
        assert valid == []
        assert errors == []
