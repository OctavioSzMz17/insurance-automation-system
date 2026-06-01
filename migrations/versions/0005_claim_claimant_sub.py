"""Add claims.claimant_sub — the insured client a claim is filed on behalf of.

`submitted_by_sub` records the OPERATOR who filed the claim; in the
"operator on behalf of a client" flow that is not the policyholder. This column
captures the actual claimant (a `clients` email) so monitoring/reporting can
surface the real insured customer, not the staff submitter.

Nullable: legacy rows and self-submissions leave it empty.

Revision ID: 0005_claim_claimant
Revises: 0004_split_clients_staff
Create Date: 2026-05-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_claim_claimant"
down_revision: Union[str, None] = "0004_split_clients_staff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("claimant_sub", sa.String(length=255), nullable=True))
    op.create_index("ix_claims_claimant_sub", "claims", ["claimant_sub"])


def downgrade() -> None:
    op.drop_index("ix_claims_claimant_sub", table_name="claims")
    op.drop_column("claims", "claimant_sub")
