"""Persist the agent's analysis durably on the claim.

Until now the analyst's narrative, evidence and the deterministic review flags
lived ONLY in Redis (5-min TTL) and were lost on refresh/expiry. These columns
make them durable, written at analyze time regardless of the HITL outcome:

  • review_flags     — deterministic red flags (expired coverage, identity mismatch)
  • evidence_quotes  — verbatim quotes the agent extracted from the PDF
  • ai_summary       — the AI-generated triage narrative

Revision ID: 0006_claim_analysis
Revises: 0005_claim_claimant
Create Date: 2026-05-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_claim_analysis"
down_revision: Union[str, None] = "0005_claim_claimant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("claims", sa.Column(
        "review_flags", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default=sa.text("'[]'::jsonb"),
    ))
    op.add_column("claims", sa.Column(
        "evidence_quotes", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default=sa.text("'[]'::jsonb"),
    ))
    op.add_column("claims", sa.Column("ai_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "ai_summary")
    op.drop_column("claims", "evidence_quotes")
    op.drop_column("claims", "review_flags")
