"""Unit tests for get_user_by_id tool — A4.1

DB engine is fully mocked — no Neon, no .env required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Set up paths before importing the tool
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.get_user_by_id import get_user_by_id  # noqa: E402


def _mock_engine(row: MagicMock | None) -> MagicMock:
    """Build a mock engine that returns `row` from fetchone()."""
    engine = MagicMock()
    conn   = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = row
    conn.execute.return_value    = result
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    engine.connect.return_value  = conn
    return engine


def _found_row() -> MagicMock:
    row = MagicMock()
    row.user_id = 101
    row.name    = "Juan Pérez"
    row.email   = "juan.p@email.com"
    return row


# ── happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_correct_dict() -> None:
    engine = _mock_engine(_found_row())
    with patch("tools.get_user_by_id.get_engine", return_value=engine):
        result = await get_user_by_id(101)

    assert result["user_id"] == 101
    assert result["name"]    == "Juan Pérez"
    assert result["email"]   == "juan.p@email.com"


@pytest.mark.asyncio
async def test_user_id_is_int() -> None:
    engine = _mock_engine(_found_row())
    with patch("tools.get_user_by_id.get_engine", return_value=engine):
        result = await get_user_by_id(101)

    assert isinstance(result["user_id"], int)


@pytest.mark.asyncio
async def test_all_fields_are_strings_except_id() -> None:
    engine = _mock_engine(_found_row())
    with patch("tools.get_user_by_id.get_engine", return_value=engine):
        result = await get_user_by_id(101)

    assert isinstance(result["name"],  str)
    assert isinstance(result["email"], str)


# ── error paths ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_not_found_raises_value_error() -> None:
    engine = _mock_engine(None)  # fetchone() returns None
    with patch("tools.get_user_by_id.get_engine", return_value=engine):
        with pytest.raises(ValueError, match="not found"):
            await get_user_by_id(999)


@pytest.mark.asyncio
async def test_db_error_raises_runtime_error() -> None:
    engine = MagicMock()
    engine.connect.side_effect = Exception("connection refused")
    with patch("tools.get_user_by_id.get_engine", return_value=engine):
        with pytest.raises(RuntimeError, match="Database query failed"):
            await get_user_by_id(101)
