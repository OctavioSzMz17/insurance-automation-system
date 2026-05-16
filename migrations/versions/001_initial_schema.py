"""Initial schema: users, policies, claims tables with enums, indexes, and triggers.

Revision ID: 001
Revises:
Create Date: 2026-05-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial schema using raw SQL for better control over complex DDL.

    Order:
    1. ENUMs
    2. Tables with FKs and constraints
    3. Indexes
    4. Triggers and functions
    """

    # ============================================================
    # 1. CREATE ENUM TYPES
    # ============================================================

    op.execute('''
        CREATE TYPE policy_status AS ENUM (
            'ACTIVE',
            'EXPIRED',
            'PENDING_PAYMENT',
            'CANCELLED'
        );
    ''')

    op.execute('''
        CREATE TYPE severity_label AS ENUM (
            'LOW',
            'HIGH',
            'TOTAL_LOSS'
        );
    ''')

    op.execute('''
        CREATE TYPE claim_status AS ENUM (
            'APPROVED',
            'REJECTED',
            'MANUAL_REVIEW',
            'AWAITING_INPUT'
        );
    ''')

    # ============================================================
    # 2. CREATE TABLES
    # ============================================================

    op.execute('''
        CREATE TABLE users (
            user_id BIGSERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(320) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    ''')

    op.execute('''
        CREATE TABLE policies (
            policy_id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            type VARCHAR(120) NOT NULL,
            status policy_status NOT NULL DEFAULT 'PENDING_PAYMENT',
            deductible_usd NUMERIC(12, 2) NOT NULL CHECK (deductible_usd >= 0),
            coverage_limit_usd NUMERIC(14, 2) NOT NULL CHECK (coverage_limit_usd > 0),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_policy_dates CHECK (end_date > start_date),
            CONSTRAINT chk_deductible_vs_limit CHECK (deductible_usd < coverage_limit_usd)
        );
    ''')

    op.execute('''
        CREATE TABLE claims (
            claim_id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            policy_id BIGINT NOT NULL REFERENCES policies(policy_id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            incident_date DATE NOT NULL,
            incident_description TEXT NOT NULL,
            incident_location TEXT,
            severity_label severity_label NOT NULL,
            reported_damage_amount NUMERIC(14, 2) CHECK (
                reported_damage_amount IS NULL OR reported_damage_amount >= 0
            ),
            claim_status claim_status NOT NULL DEFAULT 'AWAITING_INPUT',
            ai_reasoning TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    ''')

    # ============================================================
    # 3. CREATE INDEXES
    # ============================================================

    op.execute('CREATE INDEX idx_policies_user_id ON policies(user_id);')
    op.execute('CREATE INDEX idx_claims_user_id ON claims(user_id);')
    op.execute('CREATE INDEX idx_claims_policy_id ON claims(policy_id);')
    op.execute('''
        CREATE INDEX idx_claims_status ON claims(claim_status)
        WHERE claim_status IN ('MANUAL_REVIEW', 'AWAITING_INPUT');
    ''')

    # ============================================================
    # 4. CREATE TRIGGER FUNCTION & TRIGGERS
    # ============================================================

    op.execute('''
        CREATE OR REPLACE FUNCTION trg_set_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$;
    ''')

    op.execute('''
        CREATE TRIGGER trg_users_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
    ''')

    op.execute('''
        CREATE TRIGGER trg_policies_updated_at
            BEFORE UPDATE ON policies
            FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
    ''')

    op.execute('''
        CREATE TRIGGER trg_claims_updated_at
            BEFORE UPDATE ON claims
            FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
    ''')


def downgrade() -> None:
    """Downgrade: drop all objects in reverse dependency order."""

    # ============================================================
    # 1. DROP TRIGGERS
    # ============================================================

    op.execute('DROP TRIGGER IF EXISTS trg_claims_updated_at ON claims;')
    op.execute('DROP TRIGGER IF EXISTS trg_policies_updated_at ON policies;')
    op.execute('DROP TRIGGER IF EXISTS trg_users_updated_at ON users;')

    # ============================================================
    # 2. DROP FUNCTION
    # ============================================================

    op.execute('DROP FUNCTION IF EXISTS trg_set_updated_at();')

    # ============================================================
    # 3. DROP INDEXES
    # ============================================================

    op.execute('DROP INDEX IF EXISTS idx_claims_status;')
    op.execute('DROP INDEX IF EXISTS idx_claims_policy_id;')
    op.execute('DROP INDEX IF EXISTS idx_claims_user_id;')
    op.execute('DROP INDEX IF EXISTS idx_policies_user_id;')

    # ============================================================
    # 4. DROP TABLES (reverse dependency order)
    # ============================================================

    op.execute('DROP TABLE IF EXISTS claims;')
    op.execute('DROP TABLE IF EXISTS policies;')
    op.execute('DROP TABLE IF EXISTS users;')

    # ============================================================
    # 5. DROP ENUM TYPES
    # ============================================================

    op.execute('DROP TYPE IF EXISTS claim_status;')
    op.execute('DROP TYPE IF EXISTS severity_label;')
    op.execute('DROP TYPE IF EXISTS policy_status;')
