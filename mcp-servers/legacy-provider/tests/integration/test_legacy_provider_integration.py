"""Integration tests for Legacy Provider MCP tools — A4.2

These tests hit the REAL Neon PostgreSQL database.
Requirements:
    - .env must exist with a valid DATABASE_URL
    - Data from A2.2 must be loaded (users 101-105, policies 1-5)

Run:
    py -m pytest mcp-servers/legacy-provider/tests/integration/ -v --asyncio-mode=auto

NOTE: These tests depend on seeded data. If the DB is cleared, re-run
      the loader: python data/loaders/postgres_loader.py
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.get_user_by_id import get_user_by_id
from tools.get_policies_by_user_id import get_policies_by_user_id


# ── get_user_by_id ─────────────────────────────────────────────────

class TestGetUserByIdIntegration:

    @pytest.mark.asyncio
    async def test_known_user_returns_correct_fields(self) -> None:
        result = await get_user_by_id(101)

        assert result["user_id"] == 101
        assert isinstance(result["name"],  str)
        assert isinstance(result["email"], str)
        assert "@" in result["email"]
        assert len(result["name"]) > 0

    @pytest.mark.asyncio
    async def test_known_user_returns_juan(self) -> None:
        result = await get_user_by_id(101)

        assert result["name"]  == "Juan Pérez"
        assert result["email"] == "juan.p@email.com"

    @pytest.mark.asyncio
    async def test_user_id_is_int(self) -> None:
        result = await get_user_by_id(101)
        assert isinstance(result["user_id"], int)

    @pytest.mark.asyncio
    async def test_unknown_user_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            await get_user_by_id(999999)

    @pytest.mark.asyncio
    async def test_multiple_users_exist(self) -> None:
        # All five seeded users should be retrievable
        for user_id in [101, 102, 103, 104, 105]:
            result = await get_user_by_id(user_id)
            assert result["user_id"] == user_id


# ── get_policies_by_user_id ────────────────────────────────────────

class TestGetPoliciesByUserIdIntegration:

    @pytest.mark.asyncio
    async def test_known_user_has_policies(self) -> None:
        result = await get_policies_by_user_id(101)

        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_policy_has_all_required_fields(self) -> None:
        result = await get_policies_by_user_id(101)
        policy = result[0]

        required = [
            "policy_id", "user_id", "type", "status",
            "deductible_usd", "coverage_limit_usd",
            "start_date", "end_date",
        ]
        for field in required:
            assert field in policy, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_monetary_fields_are_strings(self) -> None:
        result = await get_policies_by_user_id(101)
        policy = result[0]

        assert isinstance(policy["deductible_usd"],     str)
        assert isinstance(policy["coverage_limit_usd"], str)

    @pytest.mark.asyncio
    async def test_monetary_fields_not_floats(self) -> None:
        result = await get_policies_by_user_id(101)
        policy = result[0]

        # Verify no float precision corruption (Rule 2)
        assert "." in policy["deductible_usd"]
        assert "e" not in policy["deductible_usd"].lower()  # no scientific notation

    @pytest.mark.asyncio
    async def test_date_fields_are_iso_strings(self) -> None:
        result = await get_policies_by_user_id(101)
        policy = result[0]

        # ISO format: YYYY-MM-DD
        assert len(policy["start_date"]) == 10
        assert policy["start_date"][4]   == "-"
        assert policy["start_date"][7]   == "-"
        assert len(policy["end_date"])   == 10

    @pytest.mark.asyncio
    async def test_user_id_matches_in_all_policies(self) -> None:
        result = await get_policies_by_user_id(101)

        for policy in result:
            assert policy["user_id"] == 101

    @pytest.mark.asyncio
    async def test_unknown_user_returns_empty_list(self) -> None:
        result = await get_policies_by_user_id(999999)
        assert result == []

    @pytest.mark.asyncio
    async def test_status_is_valid_string(self) -> None:
        result = await get_policies_by_user_id(101)
        valid_statuses = {"ACTIVE", "INACTIVE", "CANCELLED", "PENDING"}

        for policy in result:
            assert isinstance(policy["status"], str)
            assert policy["status"] in valid_statuses
