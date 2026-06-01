"""Deterministic fixture IDs and payloads. Imported by both the seeder and the tests."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Final

# Stable namespace — never change in committed code (changing it shifts every fixture UUID).
_NS: Final[uuid.UUID] = uuid.UUID("3f9b1d5e-0000-4000-8000-000000000001")


def _uid(label: str) -> uuid.UUID:
    return uuid.uuid5(_NS, label)


# ── System staff (the `users` table: analysts / admins / auditors) ───────────
ANALYST_ID: Final[uuid.UUID] = _uid("user:analyst")
ANALYST_EMAIL: Final[str] = "analyst@example.com"
ANALYST_SUB: Final[str] = ANALYST_EMAIL  # JWT sub == email in dev

ADMIN_ID: Final[uuid.UUID] = _uid("user:admin")
ADMIN_EMAIL: Final[str] = "admin@example.com"
ADMIN_SUB: Final[str] = ADMIN_EMAIL

# Project developer / operator.
OCTAVIO_ID: Final[uuid.UUID] = _uid("user:staff:octavio")
OCTAVIO_EMAIL: Final[str] = "octavio.sanchez@insureai.com"
OCTAVIO_SUB: Final[str] = OCTAVIO_EMAIL


# ── Insured clients (source: Legacy_Insure_Data.xlsx, sheet "usuarios") ──────
JUAN_ID: Final[uuid.UUID] = _uid("user:legacy:101")
MARIA_ID: Final[uuid.UUID] = _uid("user:legacy:102")
CARLOS_ID: Final[uuid.UUID] = _uid("user:legacy:103")
ANA_M_ID: Final[uuid.UUID] = _uid("user:legacy:104")
LUIS_ID: Final[uuid.UUID] = _uid("user:legacy:105")

# JWT sub == email
JUAN_SUB: Final[str] = "juan.p@email.com"
MARIA_SUB: Final[str] = "m.garcia@email.com"
CARLOS_SUB: Final[str] = "c.rodriguez@email.com"
ANA_M_SUB: Final[str] = "ana.mtz@email.com"
LUIS_SUB: Final[str] = "luis.h@email.com"


# System operators. password_hash is None for now — dev-token auth does not
# verify a password yet (forward-looking column).
STAFF_USERS: Final[tuple[dict[str, object], ...]] = (
    {"id": ANALYST_ID, "email": ANALYST_EMAIL, "role": "ANALYST", "full_name": "Ana Lyst",   "password_hash": None},
    {"id": ADMIN_ID,   "email": ADMIN_EMAIL,   "role": "ADMIN",   "full_name": "Ada Min",    "password_hash": None},
    {"id": OCTAVIO_ID, "email": OCTAVIO_EMAIL, "role": "ADMIN",   "full_name": "Octavio Sánchez Mendoza", "password_hash": None},
)

# Insured customers — no system role.
CLIENTS: Final[tuple[dict[str, object], ...]] = (
    {"id": JUAN_ID,   "email": JUAN_SUB,   "full_name": "Juan Pérez"},
    {"id": MARIA_ID,  "email": MARIA_SUB,  "full_name": "María García"},
    {"id": CARLOS_ID, "email": CARLOS_SUB, "full_name": "Carlos Rodríguez"},
    {"id": ANA_M_ID,  "email": ANA_M_SUB,  "full_name": "Ana Martínez"},
    {"id": LUIS_ID,   "email": LUIS_SUB,   "full_name": "Luis Hernández"},
)

# IDs of staff that must NOT exist in the clients table (cleanup anchor for the
# seeder after the 0004 rename moved the old `users` rows into `clients`).
STAFF_IDS: Final[tuple[uuid.UUID, ...]] = (ANALYST_ID, ADMIN_ID, OCTAVIO_ID)


# ── Dev policies (belong to the test analyst user) ───────────────────────────
POLICY_AUTO_ID: Final[uuid.UUID] = _uid("policy:auto")
POLICY_HOME_ID: Final[uuid.UUID] = _uid("policy:home")

# ── Legacy policies (source: Legacy_Insure_Data.xlsx) ────────────────────────
POL_001_ID: Final[uuid.UUID] = _uid("policy:legacy:POL-001")
POL_002_ID: Final[uuid.UUID] = _uid("policy:legacy:POL-002")
POL_003_ID: Final[uuid.UUID] = _uid("policy:legacy:POL-003")
POL_004_ID: Final[uuid.UUID] = _uid("policy:legacy:POL-004")
POL_005_ID: Final[uuid.UUID] = _uid("policy:legacy:POL-005")


POLICIES: Final[tuple[dict[str, object], ...]] = (
    # ── dev policies (owned by an insured client, not staff) ─────────────────
    {
        "id": POLICY_AUTO_ID,
        "client_id": JUAN_ID,
        "policy_type": "Auto Insurance",
        "status": "ACTIVE",
        "deductible_usd": Decimal("500.00"),
        "coverage_limit_usd": Decimal("25000.00"),
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 12, 31),
        "coverage_rules": [
            {"code": "COLLISION", "description": "Collision damage", "included": True, "sub_limit_usd": "15000.00"},
            {"code": "THEFT", "description": "Vehicle theft", "included": True, "sub_limit_usd": "10000.00"},
            {"code": "RACE", "description": "Damage during racing events", "included": False, "sub_limit_usd": None},
        ],
    },
    {
        "id": POLICY_HOME_ID,
        "client_id": JUAN_ID,
        "policy_type": "Home Insurance",
        "status": "ACTIVE",
        "deductible_usd": Decimal("1000.00"),
        "coverage_limit_usd": Decimal("150000.00"),
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 12, 31),
        "coverage_rules": [
            {"code": "FIRE", "description": "Fire damage", "included": True, "sub_limit_usd": "100000.00"},
            {"code": "FLOOD", "description": "Flood damage", "included": False, "sub_limit_usd": None},
            {"code": "THEFT", "description": "Theft of contents", "included": True, "sub_limit_usd": "20000.00"},
        ],
    },
    # ── legacy policies ───────────────────────────────────────────────────────
    {
        "id": POL_001_ID,
        "client_id":JUAN_ID,
        "policy_type": "Premium Full Coverage",
        "status": "ACTIVE",
        "deductible_usd": Decimal("500.00"),
        "coverage_limit_usd": Decimal("50000.00"),
        "effective_from": date(2025, 1, 1),
        "effective_to": date(2026, 1, 1),
        "coverage_rules": [],
    },
    {
        "id": POL_002_ID,
        "client_id":MARIA_ID,
        "policy_type": "Basic Third Party",
        "status": "EXPIRED",
        "deductible_usd": Decimal("1000.00"),
        "coverage_limit_usd": Decimal("15000.00"),
        "effective_from": date(2023, 5, 20),
        "effective_to": date(2024, 5, 20),
        "coverage_rules": [],
    },
    {
        "id": POL_003_ID,
        "client_id":CARLOS_ID,
        "policy_type": "Standard Plus",
        "status": "ACTIVE",
        "deductible_usd": Decimal("750.00"),
        "coverage_limit_usd": Decimal("30000.00"),
        "effective_from": date(2025, 3, 15),
        "effective_to": date(2026, 3, 15),
        "coverage_rules": [],
    },
    {
        "id": POL_004_ID,
        "client_id":ANA_M_ID,
        "policy_type": "Premium Full Coverage",
        "status": "ACTIVE",
        "deductible_usd": Decimal("500.00"),
        "coverage_limit_usd": Decimal("50000.00"),
        "effective_from": date(2025, 2, 10),
        "effective_to": date(2026, 2, 10),
        "coverage_rules": [],
    },
    {
        "id": POL_005_ID,
        "client_id":LUIS_ID,
        "policy_type": "Basic Third Party",
        "status": "PENDING_PAYMENT",
        "deductible_usd": Decimal("1200.00"),
        "coverage_limit_usd": Decimal("15000.00"),
        "effective_from": date(2024, 12, 1),
        "effective_to": date(2025, 12, 1),
        "coverage_rules": [],
    },
)
