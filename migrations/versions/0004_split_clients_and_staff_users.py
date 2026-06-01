"""Split insured customers from system staff.

Before: a single `users` table held BOTH insured customers (policyholders) and
system operators, with a role CHECK of ('ANALYST','ADMIN'). Policies/claims
pointed at it via `user_id`. This conflated two very different concepts.

After:
- `clients`  — insured customers (policyholders). No system role. The old
               `users` table is renamed here, preserving all rows + FKs.
               `policies.user_id` is renamed to `policies.client_id`.
- `users`    — system staff (ANALYST / ADMIN / AUDITOR) with a `password_hash`
               for real authentication. Brand-new table.

Object names (pkey, unique key, indexes, trigger) on the old table are renamed
to `clients_*` so the freshly-created `users` table can reuse the `users_*`
auto-generated names without collision.

Revision ID: 0004_split_clients_staff
Revises: 0003_least_privilege
Create Date: 2026-05-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_split_clients_staff"
down_revision: Union[str, None] = "0003_least_privilege"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users (insured customers) → clients ────────────────────────────────
    op.execute("ALTER TABLE users RENAME TO clients")
    # Free up the 'users_*' object names for the new staff table below.
    op.execute("ALTER TABLE clients RENAME CONSTRAINT users_pkey TO clients_pkey")
    op.execute("ALTER TABLE clients RENAME CONSTRAINT users_email_key TO clients_email_key")
    op.execute("ALTER INDEX ix_users_email RENAME TO ix_clients_email")
    # Clients are insured customers — they carry no system role.
    op.drop_constraint("ck_users_role", "clients", type_="check")
    op.drop_column("clients", "role")
    # Rename the updated_at trigger to match the new table name.
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON clients")
    op.execute(
        "CREATE TRIGGER trg_clients_updated_at BEFORE UPDATE ON clients "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ── 2. policies.user_id → policies.client_id ──────────────────────────────
    op.execute("ALTER TABLE policies RENAME COLUMN user_id TO client_id")
    op.execute("ALTER TABLE policies RENAME CONSTRAINT policies_user_id_fkey TO policies_client_id_fkey")
    op.execute("ALTER INDEX ix_policies_user_id RENAME TO ix_policies_client_id")

    # ── 3. New staff users table (system operators) ───────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        # Nullable for now: dev-token auth does not set a password yet.
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('ANALYST','ADMIN','AUDITOR')", name="ck_users_role"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.execute(
        "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    # Drop the staff users table (cascades its own pkey/unique/index/check).
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users")
    op.drop_table("users")

    # policies.client_id → policies.user_id
    op.execute("ALTER INDEX ix_policies_client_id RENAME TO ix_policies_user_id")
    op.execute("ALTER TABLE policies RENAME CONSTRAINT policies_client_id_fkey TO policies_user_id_fkey")
    op.execute("ALTER TABLE policies RENAME COLUMN client_id TO user_id")

    # clients → users (insured), restoring the role column.
    op.execute("DROP TRIGGER IF EXISTS trg_clients_updated_at ON clients")
    op.add_column("clients", sa.Column("role", sa.String(length=32), nullable=False, server_default="ANALYST"))
    op.create_check_constraint("ck_users_role", "clients", "role IN ('ANALYST','ADMIN')")
    op.execute("ALTER INDEX ix_clients_email RENAME TO ix_users_email")
    op.execute("ALTER TABLE clients RENAME CONSTRAINT clients_email_key TO users_email_key")
    op.execute("ALTER TABLE clients RENAME CONSTRAINT clients_pkey TO users_pkey")
    op.execute("ALTER TABLE clients RENAME TO users")
    op.execute(
        "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.alter_column("users", "role", server_default=None)
