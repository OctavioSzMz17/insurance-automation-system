"""Authentication & authorization for the orchestrator (Rule 7).

Public API:
    Role                 — enum: analyst | auditor | admin
    UserPrincipal        — authenticated caller (sub + role)
    create_access_token  — sign a JWT (dev-only / tests)
    decode_jwt           — verify + decode a JWT, raises 401 on failure
    require_role         — FastAPI Depends() factory enforcing role gates
"""
from .roles import Role, UserPrincipal, has_role_or_higher
from .jwt import create_access_token, decode_jwt
from .dependencies import require_role

__all__ = [
    "Role",
    "UserPrincipal",
    "has_role_or_higher",
    "create_access_token",
    "decode_jwt",
    "require_role",
]
