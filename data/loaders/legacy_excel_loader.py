"""Legacy Excel Loader — A2.1

Reads Legacy_Insure_Data.xlsx, validates all rows against Pydantic schemas,
and outputs a validation report. Does NOT write to the database.
That is A2.2's responsibility.

Usage:
    python data/loaders/legacy_excel_loader.py
    python data/loaders/legacy_excel_loader.py --config path/to/config.yaml

Rules applied:
    Rule 1  — Uses Pydantic schemas from packages/common/
    Rule 9  — All steps logged via observability/logging.py
    Rule 11 — Type hints on every function
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml
from pydantic import ValidationError

# Add project root to path for package imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.common.schemas.user import UserSchema
from packages.common.schemas.policy import PolicySchema
from packages.common.enums import PolicyStatus

# Logger setup (follows Rule 9: no print() statements)
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
class ValidationError_:
    """Represents a single validation error in the report."""
    sheet: str
    row: int
    field: str
    value: Any
    error: str


@dataclass
class SheetResult:
    """Result for a single sheet validation."""
    sheet_name: str
    table: str
    rows_read: int
    rows_valid: int
    rows_invalid: int
    errors: list[ValidationError_] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Full validation report across all sheets."""
    status: str                                    # "VALID" or "INVALID"
    excel_file: str
    timestamp: str
    sheets: list[SheetResult] = field(default_factory=list)
    total_rows_valid: int = 0
    total_rows_invalid: int = 0


# ============================================================
# CONFIG LOADER
# ============================================================

def load_config(config_path: str) -> dict:
    """Load and return YAML configuration file.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        Parsed config as a dictionary.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If required keys are missing.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if "excel_file" not in config:
        raise ValueError("Config missing required key: 'excel_file'")
    if "sheets" not in config:
        raise ValueError("Config missing required key: 'sheets'")

    logger.info(f"Config loaded from: {config_path}")
    return config


# ============================================================
# EXCEL READER
# ============================================================

def read_excel_sheets(excel_path: str, sheet_config: dict) -> dict[str, pd.DataFrame]:
    """Read Excel sheets defined in config.

    Args:
        excel_path: Path to the Excel file.
        sheet_config: Dict of {table_name: {sheet_name, required_columns, ...}}

    Returns:
        Dict of {table_name: DataFrame}

    Raises:
        FileNotFoundError: If Excel file doesn't exist.
    """
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    xls = pd.ExcelFile(excel_path)
    available_sheets = xls.sheet_names
    logger.info(f"Excel file opened: {excel_path.name}")
    logger.info(f"Available sheets: {available_sheets}")

    result: dict[str, pd.DataFrame] = {}

    for table_name, cfg in sheet_config.items():
        sheet_name = cfg["sheet_name"]

        if sheet_name not in available_sheets:
            logger.warning(
                f"Sheet '{sheet_name}' not found in Excel (expected for '{table_name}'). Skipping."
            )
            continue

        df = pd.read_excel(excel_path, sheet_name=sheet_name)
        result[table_name] = df
        logger.info(f"Sheet '{sheet_name}' -> table '{table_name}': {len(df)} rows read")

    return result


# ============================================================
# COLUMN VALIDATORS
# ============================================================

def validate_required_columns(
    df: pd.DataFrame,
    table: str,
    sheet_name: str,
    required_columns: list[str]
) -> list[ValidationError_]:
    """Check that all required columns exist in the DataFrame.

    Args:
        df: The DataFrame to check.
        table: Table name (for error reporting).
        sheet_name: Excel sheet name (for error reporting).
        required_columns: List of expected column names.

    Returns:
        List of ValidationError_ for each missing column.
    """
    errors: list[ValidationError_] = []
    actual_columns = list(df.columns)

    for col in required_columns:
        if col not in actual_columns:
            errors.append(ValidationError_(
                sheet=sheet_name,
                row=0,
                field=col,
                value=None,
                error=f"Required column '{col}' is missing. Found columns: {actual_columns}"
            ))

    if errors:
        logger.error(f"Column validation failed for '{table}': {[e.field for e in errors]}")
    else:
        logger.info(f"Column check passed for '{table}': all required columns present")

    return errors


# ============================================================
# ROW VALIDATORS
# ============================================================

def validate_users(
    df: pd.DataFrame,
    sheet_name: str
) -> tuple[list[UserSchema], list[ValidationError_]]:
    """Validate all rows in the users sheet.

    Args:
        df: Users DataFrame.
        sheet_name: Excel sheet name for error context.

    Returns:
        Tuple of (valid_records, errors)
    """
    valid: list[UserSchema] = []
    errors: list[ValidationError_] = []
    seen_emails: set[str] = set()

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # +2: 1 for header, 1 for 1-based index

        try:
            # Build and validate via Pydantic
            user = UserSchema(
                user_id=int(row["user_id"]),
                name=str(row["name"]),
                email=str(row["email"])
            )

            # Check for duplicate emails within the sheet
            if user.email in seen_emails:
                errors.append(ValidationError_(
                    sheet=sheet_name,
                    row=row_num,
                    field="email",
                    value=user.email,
                    error=f"Duplicate email '{user.email}' found in sheet"
                ))
                continue

            seen_emails.add(user.email)
            valid.append(user)

        except (ValidationError, ValueError, TypeError) as e:
            errors.append(ValidationError_(
                sheet=sheet_name,
                row=row_num,
                field="multiple",
                value=row.to_dict(),
                error=str(e)
            ))

    return valid, errors


def _safe_decimal(value: Any, field_name: str) -> Decimal:
    """Convert a value to Decimal safely.

    Args:
        value: Raw value from Excel.
        field_name: Field name for error context.

    Returns:
        Decimal representation.

    Raises:
        ValueError: If conversion fails.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"Cannot convert '{value}' to Decimal for field '{field_name}'")


def _safe_date(value: Any, field_name: str) -> date:
    """Convert a value to date safely.

    Args:
        value: Raw value from Excel (may be str, datetime, pd.Timestamp).
        field_name: Field name for error context.

    Returns:
        date object.

    Raises:
        ValueError: If conversion fails.
    """
    if isinstance(value, datetime):  # datetime subclasses date — check it first
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date") and callable(value.date):
        return value.date()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(f"Cannot parse '{value}' as a date for field '{field_name}'. Expected YYYY-MM-DD")


def validate_policies(
    df: pd.DataFrame,
    sheet_name: str,
    valid_user_ids: set[int]
) -> tuple[list[PolicySchema], list[ValidationError_]]:
    """Validate all rows in the policies sheet.

    Args:
        df: Policies DataFrame.
        sheet_name: Excel sheet name for error context.
        valid_user_ids: Set of valid user_ids from the users sheet (FK check).

    Returns:
        Tuple of (valid_records, errors)
    """
    valid: list[PolicySchema] = []
    errors: list[ValidationError_] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 2

        try:
            # FK check: user_id must reference a known user
            user_id = int(row["user_id"])
            if user_id not in valid_user_ids:
                errors.append(ValidationError_(
                    sheet=sheet_name,
                    row=row_num,
                    field="user_id",
                    value=user_id,
                    error=f"user_id {user_id} does not exist in users sheet (FK violation)"
                ))
                continue

            # Build and validate via Pydantic
            policy = PolicySchema(
                policy_id=str(row["policy_id"]),
                user_id=user_id,
                type=str(row["type"]),
                status=PolicyStatus(str(row["status"])),
                deductible_usd=_safe_decimal(row["deductible_usd"], "deductible_usd"),
                coverage_limit_usd=_safe_decimal(row["coverage_limit_usd"], "coverage_limit_usd"),
                start_date=_safe_date(row["start_date"], "start_date"),
                end_date=_safe_date(row["end_date"], "end_date")
            )

            valid.append(policy)

        except (ValidationError, ValueError, TypeError) as e:
            errors.append(ValidationError_(
                sheet=sheet_name,
                row=row_num,
                field="multiple",
                value=row.to_dict(),
                error=str(e)
            ))

    return valid, errors


# ============================================================
# REPORT GENERATOR
# ============================================================

def save_report(report: ValidationReport, output_path: str) -> None:
    """Serialize and save validation report to JSON.

    Args:
        report: ValidationReport dataclass instance.
        output_path: Path to save the JSON report.
    """
    def serialize(obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        return str(obj)

    report_dict = {
        "status": report.status,
        "excel_file": report.excel_file,
        "timestamp": report.timestamp,
        "total_rows_valid": report.total_rows_valid,
        "total_rows_invalid": report.total_rows_invalid,
        "sheets": [
            {
                "sheet_name": s.sheet_name,
                "table": s.table,
                "rows_read": s.rows_read,
                "rows_valid": s.rows_valid,
                "rows_invalid": s.rows_invalid,
                "errors": [
                    {
                        "row": e.row,
                        "field": e.field,
                        "value": serialize(e.value),
                        "error": e.error
                    }
                    for e in s.errors
                ]
            }
            for s in report.sheets
        ]
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False, default=serialize)

    logger.info(f"Validation report saved: {output_path}")


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def run(config_path: str) -> ValidationReport:
    """Run the full validation pipeline.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        ValidationReport with all results.
    """
    logger.info("=" * 60)
    logger.info("Legacy Excel Loader — Validation Phase (A2.1)")
    logger.info("=" * 60)

    # 1. Load config
    config = load_config(config_path)
    excel_path = config["excel_file"]
    sheet_config = config["sheets"]
    report_path = config.get("validation_report_path", "data/validation_report.json")

    # 2. Read sheets
    sheets_data = read_excel_sheets(excel_path, sheet_config)

    report = ValidationReport(
        status="VALID",
        excel_file=str(excel_path),
        timestamp=datetime.now(timezone.utc).isoformat()
    )

    valid_user_ids: set[int] = set()

    # 3. Validate users (must run before policies for FK checks)
    if "users" in sheets_data:
        df_users = sheets_data["users"]
        cfg = sheet_config["users"]
        sheet_name = cfg["sheet_name"]

        # Check columns first
        col_errors = validate_required_columns(df_users, "users", sheet_name, cfg["required_columns"])
        if col_errors:
            sheet_result = SheetResult(
                sheet_name=sheet_name,
                table="users",
                rows_read=len(df_users),
                rows_valid=0,
                rows_invalid=len(df_users),
                errors=col_errors
            )
        else:
            valid_users, row_errors = validate_users(df_users, sheet_name)
            valid_user_ids = {u.user_id for u in valid_users}
            sheet_result = SheetResult(
                sheet_name=sheet_name,
                table="users",
                rows_read=len(df_users),
                rows_valid=len(valid_users),
                rows_invalid=len(row_errors),
                errors=row_errors
            )
            logger.info(f"Users: {len(valid_users)} valid, {len(row_errors)} invalid")

        report.sheets.append(sheet_result)

    # 4. Validate policies (depends on users for FK check)
    if "policies" in sheets_data:
        df_policies = sheets_data["policies"]
        cfg = sheet_config["policies"]
        sheet_name = cfg["sheet_name"]

        col_errors = validate_required_columns(df_policies, "policies", sheet_name, cfg["required_columns"])
        if col_errors:
            sheet_result = SheetResult(
                sheet_name=sheet_name,
                table="policies",
                rows_read=len(df_policies),
                rows_valid=0,
                rows_invalid=len(df_policies),
                errors=col_errors
            )
        else:
            valid_policies, row_errors = validate_policies(df_policies, sheet_name, valid_user_ids)
            sheet_result = SheetResult(
                sheet_name=sheet_name,
                table="policies",
                rows_read=len(df_policies),
                rows_valid=len(valid_policies),
                rows_invalid=len(row_errors),
                errors=row_errors
            )
            logger.info(f"Policies: {len(valid_policies)} valid, {len(row_errors)} invalid")

        report.sheets.append(sheet_result)

    # 5. Compute totals and final status
    report.total_rows_valid = sum(s.rows_valid for s in report.sheets)
    report.total_rows_invalid = sum(s.rows_invalid for s in report.sheets)
    report.status = "VALID" if report.total_rows_invalid == 0 else "INVALID"

    # 6. Save report
    save_report(report, report_path)

    # 7. Summary
    logger.info("=" * 60)
    logger.info(f"Validation complete | Status: {report.status}")
    logger.info(f"Total valid rows:   {report.total_rows_valid}")
    logger.info(f"Total invalid rows: {report.total_rows_invalid}")
    logger.info("=" * 60)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy Excel Loader — Validation Phase")
    parser.add_argument(
        "--config",
        default="data/loaders/loader_config.yaml",
        help="Path to loader_config.yaml (default: data/loaders/loader_config.yaml)"
    )
    args = parser.parse_args()

    report = run(args.config)
    sys.exit(0 if report.status == "VALID" else 1)
