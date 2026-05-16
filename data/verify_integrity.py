"""Data Integrity Verification — A4.3

Runs a series of read-only checks against the loaded Neon PostgreSQL
data to confirm consistency. Useful after every load and before every
deploy.

Usage:
    py data/verify_integrity.py
    py data/verify_integrity.py --report-path data/validation_report.json

Exit codes:
    0  — all checks passed
    1  — one or more checks failed

Rules applied:
    Rule 2  — Read-only DB access; monetary fields validated as positive
    Rule 8  — DATABASE_URL read from .env, never hardcoded
    Rule 9  — Structured logging; no print() for results (uses logger)
    Rule 11 — Type hints on every function
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.common.enums import PolicyStatus  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── data classes ───────────────────────────────────────────────────

@dataclass
class CheckResult:
    name:    str
    passed:  bool
    detail:  str = ""


@dataclass
class IntegrityReport:
    checks:    list[CheckResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)

    @property
    def all_passed(self) -> bool:
        return self.passed_count == self.total_count


# ── helpers ────────────────────────────────────────────────────────

def get_engine() -> Engine:
    """Build SQLAlchemy engine from DATABASE_URL."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set — check .env")
    return create_engine(db_url, echo=False, pool_pre_ping=True)


def load_validation_report(path: str) -> dict:
    """Load the A2.1 validation report (for expected row counts)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Validation report not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def expected_count(report: dict, table: str) -> int:
    """Extract the rows_valid count for a table from the validation report."""
    for sheet in report.get("sheets", []):
        if sheet["table"] == table:
            return int(sheet["rows_valid"])
    return 0


# ── individual checks ──────────────────────────────────────────────

def check_row_count(engine: Engine, table: str, expected: int) -> CheckResult:
    name = f"{table} count matches expected"
    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    passed = actual == expected
    return CheckResult(name, passed, f"{actual} / {expected}")


def check_no_orphan_policies(engine: Engine) -> CheckResult:
    name = "no orphan policies (FK integrity)"
    sql = text("""
        SELECT COUNT(*) FROM policies p
        LEFT JOIN users u ON p.user_id = u.user_id
        WHERE u.user_id IS NULL
    """)
    with engine.connect() as conn:
        orphans = conn.execute(sql).scalar_one()
    return CheckResult(name, orphans == 0, f"{orphans} found")


def check_no_duplicate_emails(engine: Engine) -> CheckResult:
    name = "no duplicate emails in users"
    sql = text("""
        SELECT email, COUNT(*) FROM users
        GROUP BY email HAVING COUNT(*) > 1
    """)
    with engine.connect() as conn:
        dupes = conn.execute(sql).fetchall()
    return CheckResult(name, len(dupes) == 0, f"{len(dupes)} found")


def check_positive_money(engine: Engine, field_name: str) -> CheckResult:
    name = f"all {field_name} positive"
    sql = text(f"SELECT COUNT(*) FROM policies WHERE {field_name} <= 0")
    with engine.connect() as conn:
        bad = conn.execute(sql).scalar_one()
        total = conn.execute(text("SELECT COUNT(*) FROM policies")).scalar_one()
    return CheckResult(name, bad == 0, f"{total - bad} / {total}")


def check_deductible_lt_coverage(engine: Engine) -> CheckResult:
    name = "deductible < coverage_limit on every policy"
    sql = text("""
        SELECT COUNT(*) FROM policies
        WHERE deductible_usd >= coverage_limit_usd
    """)
    with engine.connect() as conn:
        violations = conn.execute(sql).scalar_one()
        total = conn.execute(text("SELECT COUNT(*) FROM policies")).scalar_one()
    return CheckResult(name, violations == 0, f"{total - violations} / {total}")


def check_end_after_start(engine: Engine) -> CheckResult:
    name = "end_date > start_date on every policy"
    sql = text("""
        SELECT COUNT(*) FROM policies
        WHERE end_date <= start_date
    """)
    with engine.connect() as conn:
        violations = conn.execute(sql).scalar_one()
        total = conn.execute(text("SELECT COUNT(*) FROM policies")).scalar_one()
    return CheckResult(name, violations == 0, f"{total - violations} / {total}")


def check_status_values_valid(engine: Engine) -> CheckResult:
    name = "all status values are valid PolicyStatus members"
    # ENUM is enforced at the DB level, but we double-check by listing distinct values
    sql = text("SELECT DISTINCT status::text FROM policies")
    with engine.connect() as conn:
        values = {row[0] for row in conn.execute(sql).fetchall()}
    valid = {s.value for s in PolicyStatus}
    invalid = values - valid
    return CheckResult(name, len(invalid) == 0, f"invalid: {invalid or '∅'}")


# ── main runner ────────────────────────────────────────────────────

def run(report_path: str) -> IntegrityReport:
    """Run every integrity check and return the consolidated report."""
    logger.info("=" * 60)
    logger.info("Data Integrity Verification (A4.3)")
    logger.info("=" * 60)

    engine = get_engine()
    validation_report = load_validation_report(report_path)

    expected_users    = expected_count(validation_report, "users")
    expected_policies = expected_count(validation_report, "policies")

    report = IntegrityReport()
    report.checks.append(check_row_count(engine, "users",    expected_users))
    report.checks.append(check_row_count(engine, "policies", expected_policies))
    report.checks.append(check_no_orphan_policies(engine))
    report.checks.append(check_no_duplicate_emails(engine))
    report.checks.append(check_positive_money(engine, "deductible_usd"))
    report.checks.append(check_positive_money(engine, "coverage_limit_usd"))
    report.checks.append(check_deductible_lt_coverage(engine))
    report.checks.append(check_end_after_start(engine))
    report.checks.append(check_status_values_valid(engine))

    # Print result lines
    for c in report.checks:
        symbol = "PASS" if c.passed else "FAIL"
        logger.info(f"  [{symbol}] {c.name:<48} {c.detail}")

    logger.info("=" * 60)
    overall = "PASS" if report.all_passed else "FAIL"
    logger.info(
        f"DATA INTEGRITY: {overall} "
        f"({report.passed_count}/{report.total_count} checks)"
    )
    logger.info("=" * 60)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Integrity Verification (A4.3)")
    parser.add_argument(
        "--report-path",
        default="data/validation_report.json",
        help="Path to validation_report.json (default: data/validation_report.json)",
    )
    args = parser.parse_args()

    result = run(args.report_path)
    sys.exit(0 if result.all_passed else 1)
