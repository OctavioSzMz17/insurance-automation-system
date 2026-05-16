"""PostgreSQL Legacy Data Loader — A2.2

Reads validated Excel data and loads it into PostgreSQL (Neon).
Depends on A2.1 having run successfully (validation_report.json must be VALID).
Does NOT process claims — those come via agent + PDF (Phase B).

Usage:
    python data/loaders/postgres_loader.py
    python data/loaders/postgres_loader.py --config path/to/config.yaml

Rules applied:
    Rule 2  — Monetary fields use NUMERIC, managed via SQLAlchemy
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — All steps logged via structured logger
    Rule 11 — Type hints on every function, error handling on every DB op
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.common.enums import PolicyStatus
from packages.common.schemas.user import UserSchema
from packages.common.schemas.policy import PolicySchema
from data.loaders.legacy_excel_loader import (
    load_config,
    read_excel_sheets,
    validate_users,
    validate_policies,
    _safe_decimal,
    _safe_date,
)

load_dotenv()

# Logger (Rule 9: no print() statements)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
logger = logging.getLogger(__name__)


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class TableLoadResult:
    """Result for loading a single table."""
    table: str
    rows_attempted: int
    rows_loaded: int
    rows_failed: int
    errors: list[str] = field(default_factory=list)


@dataclass
class LoadReport:
    """Full report for the entire load operation."""
    status: str                 # "SUCCESS" or "FAILED"
    timestamp: str
    tables: list[TableLoadResult] = field(default_factory=list)
    total_rows_loaded: int = 0
    total_rows_failed: int = 0
    error_message: str = ""


# ============================================================
# VALIDATION REPORT CHECK
# ============================================================

def check_validation_report(report_path: str) -> None:
    """Ensure the validation report exists and is VALID.

    Args:
        report_path: Path to validation_report.json

    Raises:
        FileNotFoundError: If report doesn't exist.
        ValueError: If report status is not VALID.
    """
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Validation report not found: {report_path}\n"
            "Run A2.1 first: python data/loaders/legacy_excel_loader.py"
        )

    with open(path, "r", encoding="utf-8") as f:
        report = json.load(f)

    if report.get("status") != "VALID":
        invalid_count = report.get("total_rows_invalid", "?")
        raise ValueError(
            f"Validation report status is '{report['status']}' "
            f"({invalid_count} invalid rows). "
            f"Fix the data and re-run A2.1 before loading."
        )

    logger.info(f"Validation report: VALID ({report['total_rows_valid']} rows) ✓")


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_engine() -> Engine:
    """Create SQLAlchemy engine from DATABASE_URL.

    Returns:
        SQLAlchemy Engine connected to Neon PostgreSQL.

    Raises:
        ValueError: If DATABASE_URL is not set.
    """
    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Copy .env.example to .env and fill in your credentials."
        )

    engine = create_engine(db_url, echo=False)
    logger.info("Database engine created (Neon PostgreSQL)")
    return engine


# ============================================================
# LOADERS
# ============================================================

def load_users(conn: Any, users: list[UserSchema]) -> TableLoadResult:
    """Load users into the database using DELETE → INSERT (idempotent).

    Args:
        conn: Active SQLAlchemy connection (inside a transaction).
        users: List of validated UserSchema objects.

    Returns:
        TableLoadResult with counts and any errors.
    """
    result = TableLoadResult(
        table="users",
        rows_attempted=len(users),
        rows_loaded=0,
        rows_failed=0
    )

    # Insert each user (DELETEs are handled centrally in run() in reverse FK order)
    for user in users:
        try:
            conn.execute(
                text("""
                    INSERT INTO users (user_id, name, email)
                    VALUES (:user_id, :name, :email)
                """),
                {
                    "user_id": user.user_id,
                    "name": user.name,
                    "email": user.email
                }
            )
            result.rows_loaded += 1
            logger.info(f"  Inserted user: {user.user_id} — {user.name}")

        except Exception as e:
            result.rows_failed += 1
            error_msg = f"Failed to insert user_id={user.user_id}: {e}"
            result.errors.append(error_msg)
            logger.error(error_msg)
            raise  # Re-raise to trigger transaction rollback

    return result


def _parse_policy_id(raw_id: str) -> int:
    """Convert Excel policy_id (e.g. 'POL-001') to DB integer (e.g. 1).

    Strips any non-numeric prefix and returns the integer portion.
    Examples:
        'POL-001' → 1
        'POL-042' → 42
        '7'       → 7

    Args:
        raw_id: Raw policy_id string from Excel.

    Returns:
        Integer suitable for BIGSERIAL column.

    Raises:
        ValueError: If no numeric portion can be extracted.
    """
    # Remove any non-digit prefix (e.g. "POL-", "P-", "POLICY-")
    digits = "".join(filter(str.isdigit, str(raw_id)))
    if not digits:
        raise ValueError(f"Cannot extract numeric ID from policy_id: '{raw_id}'")
    return int(digits)


def load_policies(conn: Any, policies: list[PolicySchema]) -> TableLoadResult:
    """Load policies into the database using DELETE → INSERT (idempotent).

    Args:
        conn: Active SQLAlchemy connection (inside a transaction).
        policies: List of validated PolicySchema objects.

    Returns:
        TableLoadResult with counts and any errors.
    """
    result = TableLoadResult(
        table="policies",
        rows_attempted=len(policies),
        rows_loaded=0,
        rows_failed=0
    )

    # Convert all policy_ids to integers for DB operations
    db_policy_ids = [_parse_policy_id(p.policy_id) for p in policies]

    # Insert each policy (DELETEs are handled centrally in run() in reverse FK order)
    for policy, db_id in zip(policies, db_policy_ids):
        try:
            conn.execute(
                text("""
                    INSERT INTO policies (
                        policy_id, user_id, type, status,
                        deductible_usd, coverage_limit_usd,
                        start_date, end_date
                    )
                    VALUES (
                        :policy_id, :user_id, :type, :status,
                        :deductible_usd, :coverage_limit_usd,
                        :start_date, :end_date
                    )
                """),
                {
                    "policy_id": db_id,
                    "user_id": policy.user_id,
                    "type": policy.type,
                    "status": policy.status.value,
                    "deductible_usd": policy.deductible_usd,
                    "coverage_limit_usd": policy.coverage_limit_usd,
                    "start_date": policy.start_date,
                    "end_date": policy.end_date
                }
            )
            result.rows_loaded += 1
            logger.info(f"  Inserted policy: {policy.policy_id} (DB id={db_id}) — {policy.type} (user: {policy.user_id})")

        except Exception as e:
            result.rows_failed += 1
            error_msg = f"Failed to insert policy_id={policy.policy_id}: {e}"
            result.errors.append(error_msg)
            logger.error(error_msg)
            raise  # Re-raise to trigger transaction rollback

    return result


# ============================================================
# REPORT SAVER
# ============================================================

def save_load_report(report: LoadReport, output_path: str) -> None:
    """Save the load report to JSON.

    Args:
        report: LoadReport dataclass instance.
        output_path: Path to write the JSON report.
    """
    report_dict = {
        "status": report.status,
        "timestamp": report.timestamp,
        "total_rows_loaded": report.total_rows_loaded,
        "total_rows_failed": report.total_rows_failed,
        "error_message": report.error_message,
        "tables": [
            {
                "table": t.table,
                "rows_attempted": t.rows_attempted,
                "rows_loaded": t.rows_loaded,
                "rows_failed": t.rows_failed,
                "errors": t.errors
            }
            for t in report.tables
        ]
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    logger.info(f"Load report saved: {output_path}")


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def run(config_path: str) -> LoadReport:
    """Run the full load pipeline.

    Args:
        config_path: Path to loader_config.yaml.

    Returns:
        LoadReport with results.
    """
    logger.info("=" * 60)
    logger.info("Legacy PostgreSQL Loader (A2.2)")
    logger.info("=" * 60)

    config = load_config(config_path)
    validation_report_path = config.get("validation_report_path", "data/validation_report.json")
    load_report_path = config.get("load_report_path", "data/load_report.json")

    report = LoadReport(
        status="SUCCESS",
        timestamp=datetime.now(timezone.utc).isoformat()
    )

    try:
        # 1. Gate: validation must be VALID
        check_validation_report(validation_report_path)

        # 2. Re-read and re-validate Excel (source of truth)
        sheets_data = read_excel_sheets(config["excel_file"], config["sheets"])

        valid_users: list[UserSchema] = []
        valid_policies: list[PolicySchema] = []
        valid_user_ids: set[int] = set()

        if "users" in sheets_data:
            valid_users, _ = validate_users(sheets_data["users"], config["sheets"]["users"]["sheet_name"])
            valid_user_ids = {u.user_id for u in valid_users}

        if "policies" in sheets_data:
            valid_policies, _ = validate_policies(
                sheets_data["policies"],
                config["sheets"]["policies"]["sheet_name"],
                valid_user_ids
            )

        # 3. Load into DB — single transaction (all-or-nothing)
        engine = get_engine()

        with engine.begin() as conn:
            logger.info("Transaction started")

            # DELETE in reverse FK order (policies first, then users)
            if valid_policies:
                db_policy_ids = [_parse_policy_id(p.policy_id) for p in valid_policies]
                conn.execute(
                    text("DELETE FROM policies WHERE policy_id = ANY(:ids)"),
                    {"ids": db_policy_ids}
                )
                logger.info(f"Cleared existing policies (DB IDs: {db_policy_ids})")

            if valid_users:
                user_ids = [u.user_id for u in valid_users]
                conn.execute(
                    text("DELETE FROM users WHERE user_id = ANY(:ids)"),
                    {"ids": user_ids}
                )
                logger.info(f"Cleared existing users (IDs: {user_ids})")

            # INSERT in FK order (users first, then policies)
            if valid_users:
                logger.info(f"Loading {len(valid_users)} users...")
                user_result = load_users(conn, valid_users)
                report.tables.append(user_result)

            if valid_policies:
                logger.info(f"Loading {len(valid_policies)} policies...")
                policy_result = load_policies(conn, valid_policies)
                report.tables.append(policy_result)

            logger.info("Transaction committed")

    except Exception as e:
        report.status = "FAILED"
        report.error_message = str(e)
        logger.error(f"Load failed: {e}")

    # 4. Compute totals
    report.total_rows_loaded = sum(t.rows_loaded for t in report.tables)
    report.total_rows_failed = sum(t.rows_failed for t in report.tables)

    # 5. Save load report
    save_load_report(report, load_report_path)

    # 6. Summary
    logger.info("=" * 60)
    logger.info(f"Status:          {report.status}")
    logger.info(f"Total loaded:    {report.total_rows_loaded}")
    logger.info(f"Total failed:    {report.total_rows_failed}")
    logger.info("=" * 60)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy PostgreSQL Loader")
    parser.add_argument(
        "--config",
        default="data/loaders/loader_config.yaml",
        help="Path to loader_config.yaml"
    )
    args = parser.parse_args()

    report = run(args.config)
    sys.exit(0 if report.status == "SUCCESS" else 1)
