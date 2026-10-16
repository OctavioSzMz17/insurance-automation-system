"""The fixture world the eval suite runs against.

Client identities are reused from `scripts/seed/fixtures.py` so an eval failure can be
reproduced by hand against a seeded database. Policies, however, are redefined here with
dates **relative to today** — a suite pinned to absolute dates silently rots the moment a
policy window closes, and a CI job that starts failing on a calendar boundary teaches
everyone to ignore it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.seed.fixtures import (  # noqa: E402
    ANA_M_ID,
    ANA_M_SUB,
    CARLOS_ID,
    CARLOS_SUB,
    JUAN_ID,
    JUAN_SUB,
    MARIA_ID,
    MARIA_SUB,
    _uid,
)

ANALYST_OPERATOR_SUB = "analyst@example.com"
ADMIN_OPERATOR_SUB = "admin@example.com"

# ── Policy ids (derived from the same stable namespace as the seed fixtures) ──
POLICY_IN_FORCE = _uid("evals:policy:in-force")
POLICY_WINDOW_EXPIRED = _uid("evals:policy:window-expired")
POLICY_CANCELLED = _uid("evals:policy:cancelled")
POLICY_NOT_STARTED = _uid("evals:policy:not-started")
POLICY_OTHER_CLIENT = _uid("evals:policy:other-client")


def _iso(offset_days: int) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


@dataclass(frozen=True)
class Client:
    id: UUID
    email: str
    full_name: str


@dataclass(frozen=True)
class Policy:
    id: UUID
    client_id: UUID
    status: str
    effective_from: str
    effective_to: str
    deductible_usd: str = "500.00"
    coverage_limit_usd: str = "50000.00"
    policy_type: str = "Premium Full Coverage"
    coverage_rules: tuple[dict[str, Any], ...] = ()


CLIENTS: tuple[Client, ...] = (
    Client(JUAN_ID, JUAN_SUB, "Juan Pérez"),
    Client(MARIA_ID, MARIA_SUB, "María García"),
    Client(CARLOS_ID, CARLOS_SUB, "Carlos Rodríguez"),
    Client(ANA_M_ID, ANA_M_SUB, "Ana Martínez"),
)

POLICIES: tuple[Policy, ...] = (
    # Juan — the happy path: ACTIVE and inside its window.
    Policy(POLICY_IN_FORCE, JUAN_ID, "ACTIVE", _iso(-180), _iso(+180)),
    # Juan — ACTIVE on paper but the window closed last month.
    Policy(POLICY_WINDOW_EXPIRED, JUAN_ID, "ACTIVE", _iso(-800), _iso(-30)),
    # María — inside its window but cancelled.
    Policy(POLICY_CANCELLED, MARIA_ID, "CANCELLED", _iso(-100), _iso(+200)),
    # Carlos — ACTIVE but coverage has not begun yet.
    Policy(POLICY_NOT_STARTED, CARLOS_ID, "ACTIVE", _iso(+30), _iso(+400)),
    # Ana — used for the cross-tenant case: a valid policy owned by someone else.
    Policy(POLICY_OTHER_CLIENT, ANA_M_ID, "ACTIVE", _iso(-60), _iso(+300)),
)


@dataclass
class World:
    """Everything the fake MCP layer serves, plus the directory holding fixture PDFs."""

    upload_dir: Path
    clients: tuple[Client, ...] = CLIENTS
    policies: tuple[Policy, ...] = POLICIES
    # Staff operator identities, keyed by sub. An operator is NOT an insured client and
    # deliberately has no row in `clients` — mirroring migration 0004.
    operators: dict[str, str] = field(
        default_factory=lambda: {ANALYST_OPERATOR_SUB: "Ana Lyst", ADMIN_OPERATOR_SUB: "Ada Min"}
    )

    def client_by_email(self, email: str) -> Client | None:
        return next((c for c in self.clients if c.email == email), None)

    def client_by_id(self, client_id: UUID | str) -> Client | None:
        return next((c for c in self.clients if str(c.id) == str(client_id)), None)

    def policy_by_id(self, policy_id: UUID | str) -> Policy | None:
        return next((p for p in self.policies if str(p.id) == str(policy_id)), None)

    def policies_for(self, client_id: UUID | str) -> list[Policy]:
        return [p for p in self.policies if str(p.client_id) == str(client_id)]


def policy_as_row(policy: Policy, *, owner_email: str) -> dict[str, Any]:
    """Shape a policy exactly as `mcp-servers/policies/repository.py` returns it."""
    return {
        "id": str(policy.id),
        "user_id": str(policy.client_id),
        "owner_email": owner_email,
        "status": policy.status,
        "policy_type": policy.policy_type,
        "deductible_usd": policy.deductible_usd,
        "coverage_limit_usd": policy.coverage_limit_usd,
        "effective_from": policy.effective_from,
        "effective_to": policy.effective_to,
        "coverage_rules": list(policy.coverage_rules),
        "created_at": f"{policy.effective_from}T00:00:00+00:00",
        "updated_at": f"{policy.effective_from}T00:00:00+00:00",
    }


def client_as_row(client: Client) -> dict[str, Any]:
    """Shape a client exactly as `mcp-servers/users/repository.py` returns it."""
    return {
        "id": str(client.id),
        "email": client.email,
        "full_name": client.full_name,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
