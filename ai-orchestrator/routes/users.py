"""User & policy lookup endpoints — Phase C (Adjuster Dashboard).

Endpoints:
    GET /users/{user_id}          — analyst+ — user profile lookup
    GET /users/{user_id}/policies — analyst+ — all policies for a user

These exist so the adjuster UI can verify Lía's reasoning against the
real underlying data (e.g. when Lía claims "incident date is after the
policy end date", the human needs to see the policy dates).

Both endpoints are read-only wrappers around the legacy-provider MCP
tools (`get_user_by_id`, `get_policies_by_user_id`). They are gated by
JWT bearer auth + role check (Rule 7).

Rules applied:
    Rule 1  — Pydantic schemas imported from packages/common/
    Rule 3  — Reads only; never proxies a write through legacy-provider
    Rule 7  — Every endpoint Depends(require_role(...))
    Rule 9  — Structured logging on every request
    Rule 11 — Type hints throughout
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

_ORCH_ROOT    = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _ORCH_ROOT.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_ORCH_ROOT))

from packages.common.schemas.api import (  # noqa: E402
    UserResponse,
    PolicyResponse,
    PolicyListResponse,
)
from packages.common.business_rules import (  # noqa: E402
    compute_effective_policy_status,
)

from auth                  import Role, UserPrincipal, require_role  # noqa: E402
from core.mcp_client        import MCPClientError                     # noqa: E402
from routes.mcp_router      import MCPRouter, get_mcp_router          # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


# ── GET /users/{user_id} ─────────────────────────────────────────

@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a user profile",
    description=(
        "Read-only lookup of a single user by ID. Returned data is the "
        "minimum the adjuster UI needs to verify the AI's reasoning."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "User not found"},
        500: {"description": "Upstream lookup failed"},
    },
)
async def get_user(
    user_id:    int,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> UserResponse:
    logger.info(
        f"GET /users/{user_id} | actor={user.sub} role={user.role.value}"
    )

    try:
        row = await mcp_router.call(
            "legacy-provider", "get_user_by_id",
            caller_role=user.role,
            user_id=user_id,
        )
    except ValueError as e:
        logger.warning(f"GET /users/{user_id} not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    except MCPClientError as e:
        # Master Patch Part 4 — internal detail to logs only.
        logger.error(
            "GET /users/%s upstream failure | internal=%s",
            user_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )
    except Exception as e:
        logger.error(f"GET /users/{user_id} upstream error: {e!r}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error retrieving user",
        )

    # Fix #81 — tenant isolation for ANALYST role.
    # ANALYST == policyholder: JWT sub equals their email. They may only look
    # up their own user record.  AUDITOR and ADMIN bypass this check.
    if user.role == Role.ANALYST:
        owner_email = (row.get("email") or "").strip().lower()
        actor_sub   = user.sub.strip().lower()
        if not owner_email or owner_email != actor_sub:
            logger.warning(
                "GET /users/%s DENIED cross-user | actor=%s owner_email=%r",
                user_id, user.sub, owner_email,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorised to view this user record",
            )

    return UserResponse(**row)


# ── GET /users/{user_id}/policies ────────────────────────────────

@router.get(
    "/{user_id}/policies",
    response_model=PolicyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List policies for a user",
    description=(
        "Returns every policy attached to a given user. The adjuster UI "
        "uses this to pick out the policy linked to the current claim "
        "and verify the AI's coverage analysis."
    ),
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "User not found"},
        500: {"description": "Upstream lookup failed"},
    },
)
async def get_user_policies(
    user_id:    int,
    user:       UserPrincipal = Depends(require_role(Role.ANALYST)),
    mcp_router: MCPRouter     = Depends(get_mcp_router),
) -> PolicyListResponse:
    logger.info(
        f"GET /users/{user_id}/policies | actor={user.sub} role={user.role.value}"
    )

    # Fix #81 — tenant isolation for ANALYST role.
    # Resolve the user's email via legacy-provider and enforce that ANALYST
    # (= policyholder) can only access their own policies.
    # AUDITOR and ADMIN bypass this check.
    if user.role == Role.ANALYST:
        try:
            user_row = await mcp_router.call(
                "legacy-provider", "get_user_by_id",
                caller_role=user.role,
                user_id=user_id,
            )
            owner_email = (user_row.get("email") or "").strip().lower()
            actor_sub   = user.sub.strip().lower()
            if not owner_email or owner_email != actor_sub:
                logger.warning(
                    "GET /users/%s/policies DENIED cross-user | actor=%s owner_email=%r",
                    user_id, user.sub, owner_email,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorised to view this user's policies",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "GET /users/%s/policies ownership check failed | actor=%s err=%s",
                user_id, user.sub, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not verify ownership",
            )

    try:
        rows = await mcp_router.call(
            "legacy-provider", "get_policies_by_user_id",
            caller_role=user.role,
            user_id=user_id,
        )
    except ValueError as e:
        logger.warning(f"GET /users/{user_id}/policies not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    except MCPClientError as e:
        logger.error(
            "GET /users/%s/policies upstream failure | internal=%s",
            user_id, e.internal, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.public,
        )
    except Exception as e:
        logger.error(
            f"GET /users/{user_id}/policies upstream error: {e!r}", exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error retrieving policies",
        )

    # Stamp the effective status on each policy so the UI / agent see the
    # truth as of today. The raw value is preserved in `stored_status` so
    # auditors can spot drift between what was persisted and what we now
    # report.
    policies: list[PolicyResponse] = []
    for r in rows:
        stored = r.get("status")
        effective = compute_effective_policy_status(stored, r.get("end_date"))
        policies.append(PolicyResponse(
            **{**r, "status": effective, "stored_status": stored},
        ))

    return PolicyListResponse(
        user_id=user_id,
        policies=policies,
        total=len(policies),
    )
