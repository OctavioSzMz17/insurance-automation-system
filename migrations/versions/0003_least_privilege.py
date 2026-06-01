"""Least-privilege PostgreSQL roles for each MCP domain (Rule 3 / F4.6).

Creates three LOGIN roles with the minimum table grants each MCP actually needs:

  mcp_claims   — SELECT, INSERT, UPDATE on claims
                 (no DELETE — terminal states enforced at app layer; Rule 5)
  mcp_policies — SELECT on policies, SELECT on users
                 (policies repo JOINs users to surface owner_email for ACL)
  mcp_users    — SELECT on users

The documents MCP is filesystem-only (no DB access) — no role is created.

IMPORTANT: The passwords below are dev-only placeholders.
           Change them before deploying to staging/production and set:
             DATABASE_URL_CLAIMS=postgresql://mcp_claims:<pw>@host:5432/insurance
             DATABASE_URL_POLICIES=postgresql://mcp_policies:<pw>@host:5432/insurance
             DATABASE_URL_USERS=postgresql://mcp_users:<pw>@host:5432/insurance

Revision ID: 0003_least_privilege
Revises: 0002_policy_schema
Create Date: 2026-05-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_least_privilege"
down_revision: Union[str, None] = "0002_policy_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

_ROLES = ("mcp_claims", "mcp_policies", "mcp_users")

_DEV_PASSWORDS = {
    "mcp_claims":   "mcp_claims_dev_password",    # noqa: S106 — dev only
    "mcp_policies": "mcp_policies_dev_password",  # noqa: S106 — dev only
    "mcp_users":    "mcp_users_dev_password",     # noqa: S106 — dev only
}

# CREATE ROLE ... IF NOT EXISTS was added in PG 9.6 but only for non-LOGIN
# roles; the DO block pattern is universally safe.
_CREATE_ROLE_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role}
      WITH LOGIN
           NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION
           PASSWORD '{password}';
  END IF;
END
$$;
"""


def _create_role(name: str) -> None:
    op.execute(_CREATE_ROLE_SQL.format(role=name, password=_DEV_PASSWORDS[name]))


def _drop_role(name: str) -> None:
    op.execute(f"DROP ROLE IF EXISTS {name}")


# ── upgrade ────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    # 1. Create roles
    for role in _ROLES:
        _create_role(role)

    # 2. Allow roles to connect and use the public schema
    roles_csv = ", ".join(_ROLES)
    op.execute(f"GRANT CONNECT ON DATABASE insurance TO {roles_csv}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {roles_csv}")

    # 3. Table-level privileges — minimal, per MCP domain
    # claims MCP: full CRUD minus DELETE on claims table only
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE claims TO mcp_claims")

    # policies MCP: read-only on policies + users (JOIN for owner ACL)
    op.execute("GRANT SELECT ON TABLE policies TO mcp_policies")
    op.execute("GRANT SELECT ON TABLE users    TO mcp_policies")

    # users MCP: read-only on users table only
    op.execute("GRANT SELECT ON TABLE users TO mcp_users")

    # 4. Tighten schema default: prevent any role from creating objects in
    #    public schema without explicit grant (defence-in-depth).
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    # Restore schema default
    op.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")

    # Revoke all table grants before dropping roles
    op.execute("REVOKE ALL ON TABLE claims   FROM mcp_claims")
    op.execute("REVOKE ALL ON TABLE policies FROM mcp_policies")
    op.execute("REVOKE ALL ON TABLE users    FROM mcp_policies")
    op.execute("REVOKE ALL ON TABLE users    FROM mcp_users")

    roles_csv = ", ".join(_ROLES)
    op.execute(f"REVOKE CONNECT ON DATABASE insurance FROM {roles_csv}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {roles_csv}")

    for role in _ROLES:
        _drop_role(role)
