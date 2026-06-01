"""initial schema — users, policies, claims, documents

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25

Schema highlights (mandatory per ai-rules.md):
- All monetary fields are NUMERIC, never FLOAT (Rule 2).
- claims.submitted_by_sub NOT NULL — row-level ACL anchor (Rule 2/7).
- claims.pdf_sha256 NOT NULL + UNIQUE(submitted_by_sub, pdf_sha256) — dedup (Rule 13).
- claims.decision_metadata JSONB NOT NULL DEFAULT '{}' — audit (Rule 2).
- updated_at managed by trigger, never by application code (Rule 2).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UPDATED_AT_FN_SQL = """
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _attach_updated_at_trigger(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER trg_{table}_updated_at
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute(_UPDATED_AT_FN_SQL)

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('ANALYST','ADMIN')", name="ck_users_role"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    _attach_updated_at_trigger("users")

    # ── policies ─────────────────────────────────────────────────────────────
    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deductible_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("coverage_limit_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column("coverage_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('ACTIVE','EXPIRED','CANCELLED','SUSPENDED')", name="ck_policies_status"),
        sa.CheckConstraint("deductible_usd >= 0", name="ck_policies_deductible_nonneg"),
        sa.CheckConstraint("coverage_limit_usd >= 0", name="ck_policies_limit_nonneg"),
        sa.CheckConstraint("effective_from <= effective_to", name="ck_policies_date_order"),
    )
    op.create_index("ix_policies_user_id", "policies", ["user_id"])
    op.create_index("ix_policies_status", "policies", ["status"])
    _attach_updated_at_trigger("policies")

    # ── claims ───────────────────────────────────────────────────────────────
    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("submitted_by_sub", sa.String(length=255), nullable=False),  # Rule 2/7 ACL anchor
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("policies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),  # Rule 13 dedup
        sa.Column("reported_damage_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "decision_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="ck_claims_severity"
        ),
        sa.CheckConstraint(
            "status IN ('SUBMITTED','AWAITING_INPUT','UNDER_REVIEW','APPROVED','REJECTED','MANUAL_REVIEW')",
            name="ck_claims_status",
        ),
        sa.CheckConstraint("reported_damage_amount >= 0", name="ck_claims_damage_nonneg"),
        sa.CheckConstraint("pdf_sha256 ~ '^[0-9a-f]{64}$'", name="ck_claims_pdf_sha256_hex"),
    )
    op.create_index("ix_claims_submitted_by_sub", "claims", ["submitted_by_sub"])
    op.create_index("ix_claims_policy_id", "claims", ["policy_id"])
    op.create_index("ix_claims_status", "claims", ["status"])
    op.create_index(
        "uq_claims_submitter_pdf",
        "claims",
        ["submitted_by_sub", "pdf_sha256"],
        unique=True,
    )
    _attach_updated_at_trigger("claims")

    # ── documents ────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 52428800", name="ck_documents_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documents_sha256_hex"),
    )
    op.create_index("ix_documents_claim_id", "documents", ["claim_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_claims_updated_at ON claims")
    op.execute("DROP TRIGGER IF EXISTS trg_policies_updated_at ON policies")
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users")
    op.drop_table("documents")
    op.drop_table("claims")
    op.drop_table("policies")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
