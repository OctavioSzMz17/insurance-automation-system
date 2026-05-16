"""Unit tests for get_policies_by_user_id tool — A4.1

DB engine is fully mocked — no Neon, no .env required.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.get_policies_by_user_id import get_policies_by_user_id  # noqa: E402


def _make_row(
    policy_id: int = 1,
    user_id: int = 101,
    type_: str = "Premium Full Coverage",
    status: str = "ACTIVE",
    deductible: Decimal = Decimal("500.00"),
    coverage: Decimal = Decimal("50000.00"),
    start: date = date(2025, 1, 1),
    end: date = date(2026, 1, 1),
) -> MagicMock:
    row = MagicMock()
    row.policy_id          = policy_id
    row.user_id            = user_id
    row.type               = type_
    row.status             = status
    row.deductible_usd     = deductible
    row.coverage_limit_usd = coverage
    row.start_date         = start
    row.end_date           = end
    return row


def _mock_engine(rows: list) -> MagicMock:
    engine = MagicMock()
    conn   = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    conn.execute.return_value    = result
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    engine.connect.return_value  = conn
    return engine


# ── happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_list_of_dicts() -> None:
    engine = _mock_engine([_make_row()])
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        result = await get_policies_by_user_id(101)

    assert isinstance(result, list)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_monetary_fields_are_strings() -> None:
    engine = _mock_engine([_make_row()])
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        result = await get_policies_by_user_id(101)

    assert isinstance(result[0]["deductible_usd"],     str)
    assert isinstance(result[0]["coverage_limit_usd"], str)
    assert result[0]["deductible_usd"]     == "500.00"
    assert result[0]["coverage_limit_usd"] == "50000.00"


@pytest.mark.asyncio
async def test_date_fields_are_iso_strings() -> None:
    engine = _mock_engine([_make_row()])
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        result = await get_policies_by_user_id(101)

    assert result[0]["start_date"] == "2025-01-01"
    assert result[0]["end_date"]   == "2026-01-01"


@pytest.mark.asyncio
async def test_empty_list_when_no_policies() -> None:
    engine = _mock_engine([])  # fetchall() returns empty list
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        result = await get_policies_by_user_id(101)

    assert result == []


@pytest.mark.asyncio
async def test_multiple_policies_returned() -> None:
    rows = [_make_row(policy_id=1), _make_row(policy_id=2)]
    engine = _mock_engine(rows)
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        result = await get_policies_by_user_id(101)

    assert len(result) == 2
    assert result[0]["policy_id"] == 1
    assert result[1]["policy_id"] == 2


# ── error path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_error_raises_runtime_error() -> None:
    engine = MagicMock()
    engine.connect.side_effect = Exception("connection refused")
    with patch("tools.get_policies_by_user_id.get_engine", return_value=engine):
        with pytest.raises(RuntimeError, match="Database query failed"):
            await get_policies_by_user_id(101)
