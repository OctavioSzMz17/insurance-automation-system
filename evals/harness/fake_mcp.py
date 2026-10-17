"""In-process MCP pool stand-in that records every call.

Fidelity notes — what is real and what is faked:

* `documents` tools run the **real** repository code from `mcp-servers/documents/`,
  against the **real** generated PDFs on disk. Path safety, pypdf parsing, the 200k-char
  extraction cap and malformed-file handling are therefore genuinely exercised. This is
  the whole reason fixtures are real files rather than canned strings.
* `policies` and `users` tools re-implement their handlers' control flow but call the
  **real** ACL primitives from `mcp-servers/_common/acl.py`, so authorization behaviour is
  the production behaviour. Only the SQL is replaced by the in-memory `World`.
* `claims` write tools emulate the ACL, the write gate and the terminal-status rules, and
  record every attempt so the suite can assert that no write originates from an agent loop.

Error translation mirrors the production path exactly: a domain error becomes
`BusinessError` and anything unexpected becomes `MCPClientError("upstream_internal_error")`,
which is what `core/mcp_client._coerce_tool_result` raises after reading the `kind` field.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from _common.acl import assert_admin_or_self_user, is_admin
from documents import repository as doc_repo
from evals.catalog import World, client_as_row, policy_as_row
from packages.common.exceptions import (
    BusinessError,
    DomainError,
    ForbiddenError,
    MCPClientError,
    NotFoundError,
)

Phase = Literal["agent", "verification", "mutation"]

_TERMINAL = frozenset({"APPROVED", "REJECTED", "MANUAL_REVIEW"})
_VALID_STATUSES = frozenset({
    "SUBMITTED", "AWAITING_INPUT", "UNDER_REVIEW", "APPROVED", "REJECTED", "MANUAL_REVIEW",
})
_WRITE_TOOLS = frozenset({"create_claim", "update_claim_status"})


@dataclass
class ToolCall:
    domain: str
    tool: str
    args: dict[str, Any]
    phase: Phase
    failed_with: str | None = None

    @property
    def is_write(self) -> bool:
        return self.domain == "claims" and self.tool in _WRITE_TOOLS


@dataclass
class RecordingMCPPool:
    """Implements the single method the orchestrator uses: `await pool.call(...)`."""

    world: World
    calls: list[ToolCall] = field(default_factory=list)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Set by the harness so each recorded call is attributable to a pipeline stage.
    phase: Phase = "agent"

    # ── public surface ──────────────────────────────────────────────────────

    async def call(self, domain: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        record = ToolCall(domain=domain, tool=tool, args=dict(arguments), phase=self.phase)
        self.calls.append(record)
        try:
            return await self._dispatch(domain, tool, dict(arguments))
        except (DomainError, ValueError) as exc:
            # Mirrors _common/errors.classified → kind=business → BusinessError.
            record.failed_with = str(exc)
            raise BusinessError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — mirrors kind=internal
            record.failed_with = "upstream_internal_error"
            raise MCPClientError(public="upstream_internal_error") from exc

    @property
    def write_calls(self) -> list[ToolCall]:
        return [c for c in self.calls if c.is_write]

    def calls_in(self, phase: Phase) -> list[ToolCall]:
        return [c for c in self.calls if c.phase == phase]

    # ── dispatch ────────────────────────────────────────────────────────────

    async def _dispatch(self, domain: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_{domain}__{tool}", None)
        if handler is None:
            raise ValueError(f"unknown tool: {domain}/{tool}")
        return await handler(args)

    @staticmethod
    def _identity(args: dict[str, Any]) -> tuple[str, str]:
        sub = args.get("caller_sub")
        role = args.get("caller_role")
        if not sub or not role:
            raise ValueError("caller_sub and caller_role are required")
        return str(sub), str(role)

    # ── documents (real code, real files) ───────────────────────────────────

    async def _documents__parse_pdf(self, args: dict[str, Any]) -> dict[str, Any]:
        self._identity(args)
        return doc_repo.parse_pdf(self._upload_dir(), self._pipeline_id(args))

    async def _documents__extract_text(self, args: dict[str, Any]) -> dict[str, Any]:
        self._identity(args)
        return doc_repo.extract_text(self._upload_dir(), self._pipeline_id(args))

    async def _documents__compute_sha256(self, args: dict[str, Any]) -> dict[str, Any]:
        self._identity(args)
        return doc_repo.compute_sha256(self._upload_dir(), self._pipeline_id(args))

    def _upload_dir(self) -> Path:
        return self.world.upload_dir

    @staticmethod
    def _pipeline_id(args: dict[str, Any]) -> UUID:
        raw = args.get("pipeline_id")
        if raw is None:
            raise ValueError("pipeline_id is required")
        return raw if isinstance(raw, UUID) else UUID(str(raw))

    # ── users (real ACL) ────────────────────────────────────────────────────

    async def _users__get_user_by_email(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        email = str(args.get("email") or "")
        if not email:
            raise ValueError("email is required")
        # Production behaviour: the ACL check runs BEFORE the lookup, so a non-self email
        # is refused whether or not it exists (anti-enumeration).
        assert_admin_or_self_user(caller_sub=sub, caller_role=role, target_sub=email)
        client = self.world.client_by_email(email)
        if client is None:
            raise NotFoundError("user not found")
        return {"user": client_as_row(client)}

    async def _users__get_user_by_id(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        client = self.world.client_by_id(str(args.get("user_id") or ""))
        if client is None:
            raise NotFoundError("user not found")
        if not is_admin(role) and client.email != sub:
            raise ForbiddenError("forbidden")
        return {"user": client_as_row(client)}

    async def _users__list_clients(self, args: dict[str, Any]) -> dict[str, Any]:
        _, role = self._identity(args)
        if not is_admin(role):
            raise ForbiddenError("forbidden")
        return {"clients": [client_as_row(c) for c in self.world.clients]}

    # ── policies (real ACL) ─────────────────────────────────────────────────

    async def _policies__get_policy_by_id(self, args: dict[str, Any]) -> dict[str, Any]:
        row = self._policy_row_for_caller(args)
        row.pop("owner_email", None)
        return {"policy": row}

    async def _policies__get_coverage_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        row = self._policy_row_for_caller(args)
        return {"policy_id": row["id"], "coverage_rules": row["coverage_rules"]}

    async def _policies__get_policies_by_user_id(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        client = self.world.client_by_id(str(args.get("user_id") or ""))
        if client is None:
            raise NotFoundError("user not found")
        assert_admin_or_self_user(caller_sub=sub, caller_role=role, target_sub=client.email)
        rows = []
        for policy in self.world.policies_for(client.id):
            row = policy_as_row(policy, owner_email=client.email)
            row.pop("owner_email", None)
            rows.append(row)
        return {"policies": rows}

    def _policy_row_for_caller(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        policy = self.world.policy_by_id(str(args.get("policy_id") or ""))
        if policy is None:
            raise NotFoundError("policy not found")
        owner = self.world.client_by_id(policy.client_id)
        owner_email = owner.email if owner else ""
        if not is_admin(role) and owner_email != sub:
            raise ForbiddenError("forbidden")
        return policy_as_row(policy, owner_email=owner_email)

    # ── claims ──────────────────────────────────────────────────────────────

    async def _claims__get_claim_by_id(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        row = self.rows.get(str(args.get("claim_id") or ""))
        if row is None:
            raise NotFoundError("claim not found")
        if not is_admin(role) and row["submitted_by_sub"] != sub:
            raise ForbiddenError("forbidden")
        return {"claim": row}

    async def _claims__list_claims_by_submitter(self, args: dict[str, Any]) -> dict[str, Any]:
        sub, role = self._identity(args)
        rows = list(self.rows.values())
        if not is_admin(role):
            rows = [r for r in rows if r["submitted_by_sub"] == sub]
        return {"claims": rows, "filtered_by_sub": "*" if is_admin(role) else sub}

    async def _claims__create_claim(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_write_enabled()
        sub, role = self._identity(args)
        submitted_by = str(args.get("submitted_by_sub") or "")
        if not is_admin(role) and submitted_by != sub:
            raise ForbiddenError("forbidden")
        status = str(args.get("status") or "SUBMITTED")
        if status not in _VALID_STATUSES:
            raise ValueError("invalid status")
        sha = str(args.get("pdf_sha256") or "")
        # Emulates UNIQUE (submitted_by_sub, pdf_sha256) — the dedup index from 0001.
        for existing in self.rows.values():
            if existing["submitted_by_sub"] == submitted_by and existing["pdf_sha256"] == sha:
                raise BusinessError("duplicate claim for this submitter and document")
        now = datetime.now(tz=timezone.utc).isoformat()
        row = {
            "id": str(uuid4()),
            "submitted_by_sub": submitted_by,
            "claimant_sub": args.get("claimant_sub"),
            "policy_id": str(args.get("policy_id") or ""),
            "pdf_sha256": sha,
            "reported_damage_amount": str(args.get("reported_damage_amount") or "0"),
            "severity": str(args.get("severity") or ""),
            "status": status,
            "decision_metadata": dict(args.get("decision_metadata") or {}),
            "review_flags": list(args.get("review_flags") or []),
            "evidence_quotes": list(args.get("evidence_quotes") or []),
            "ai_summary": args.get("ai_summary"),
            "created_at": now,
            "updated_at": now,
        }
        self.rows[row["id"]] = row
        return {"claim": row}

    async def _claims__update_claim_status(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_write_enabled()
        sub, role = self._identity(args)
        claim_id = str(args.get("claim_id") or "")
        row = self.rows.get(claim_id)
        if row is None:
            raise NotFoundError("claim not found")
        new_status = str(args.get("new_status") or "")
        if new_status not in _VALID_STATUSES:
            raise ValueError("invalid new_status")
        if not is_admin(role) and row["submitted_by_sub"] != sub:
            raise ForbiddenError("forbidden")
        if row["status"] == "MANUAL_REVIEW":
            raise ForbiddenError("claim is in MANUAL_REVIEW; transition refused")
        if row["status"] in _TERMINAL and new_status != row["status"]:
            raise ForbiddenError("claim is in a terminal status; transition refused")
        row["status"] = new_status
        merged = dict(row["decision_metadata"])
        merged.update(dict(args.get("decision_metadata") or {}))
        row["decision_metadata"] = merged
        row["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        return {"claim": row}

    @staticmethod
    def _assert_write_enabled() -> None:
        """Mirrors the @write_restricted decorator on the real handlers."""
        if os.environ.get("MCP_WRITE_ENABLED", "0") != "1":
            raise ForbiddenError("write operations are disabled in this deployment")
