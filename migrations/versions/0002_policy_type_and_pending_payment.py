"""Add policies.policy_type column and PENDING_PAYMENT to status constraint.

Revision ID: 0002_policy_schema
Revises: 0001_initial
Create Date: 2026-05-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_policy_schema"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add policy_type column (nullable so existing rows are not broken).
    op.add_column("policies", sa.Column("policy_type", sa.String(length=100), nullable=True))

    # Expand status CHECK constraint to include PENDING_PAYMENT.
    # PostgreSQL requires drop-then-recreate; you cannot ALTER a check constraint in place.
    op.drop_constraint("ck_policies_status", "policies", type_="check")
    op.create_check_constraint(
        "ck_policies_status",
        "policies",
        "status IN ('ACTIVE','EXPIRED','CANCELLED','SUSPENDED','PENDING_PAYMENT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_policies_status", "policies", type_="check")
    op.create_check_constraint(
        "ck_policies_status",
        "policies",
        "status IN ('ACTIVE','EXPIRED','CANCELLED','SUSPENDED')",
    )
    op.drop_column("policies", "policy_type")
