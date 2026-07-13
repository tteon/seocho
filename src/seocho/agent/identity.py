"""Auditable agent principals and bounded delegation grants."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    principal_id_hash: str
    action: str
    resource: str
    allowed: bool
    reason: str
    policy_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "principal_id_hash": self.principal_id_hash,
            "action": self.action,
            "resource": self.resource,
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    """One workspace-scoped agent identity; no bearer secret is retained."""

    principal_id: str
    workspace_id: str
    roles: frozenset[str]
    allowed_actions: frozenset[str]
    allowed_resources: frozenset[str]
    policy_version: str
    expires_at: datetime | None = None
    delegator_id: str = ""
    delegation_id: str = ""

    def __post_init__(self) -> None:
        if not self.principal_id.strip() or not self.workspace_id.strip():
            raise ValueError("principal_id and workspace_id are required")
        if not self.policy_version.strip():
            raise ValueError("policy_version is required")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    @property
    def principal_id_hash(self) -> str:
        return hashlib.sha256(self.principal_id.encode("utf-8")).hexdigest()[:16]

    def authorize(
        self,
        *,
        action: str,
        resource: str,
        workspace_id: str,
        at: datetime | None = None,
    ) -> AuthorizationDecision:
        reason = "allowed"
        allowed = True
        if workspace_id != self.workspace_id:
            allowed, reason = False, "workspace_mismatch"
        elif self.expires_at is not None and (at or _now()) >= self.expires_at:
            allowed, reason = False, "principal_expired"
        elif action not in self.allowed_actions and "*" not in self.allowed_actions:
            allowed, reason = False, "action_denied"
        elif resource not in self.allowed_resources and "*" not in self.allowed_resources:
            allowed, reason = False, "resource_denied"
        return AuthorizationDecision(
            principal_id_hash=self.principal_id_hash,
            action=action,
            resource=resource,
            allowed=allowed,
            reason=reason,
            policy_version=self.policy_version,
        )

    def delegate(
        self,
        *,
        principal_id: str,
        delegation_id: str,
        roles: Iterable[str],
        allowed_actions: Iterable[str],
        allowed_resources: Iterable[str],
        expires_at: datetime,
    ) -> "AgentPrincipal":
        actions = frozenset(allowed_actions)
        resources = frozenset(allowed_resources)
        roles_set = frozenset(roles)
        if "*" not in self.allowed_actions and not actions <= self.allowed_actions:
            raise ValueError("delegated actions exceed parent authority")
        if "*" not in self.allowed_resources and not resources <= self.allowed_resources:
            raise ValueError("delegated resources exceed parent authority")
        if not roles_set <= self.roles:
            raise ValueError("delegated roles exceed parent roles")
        if self.expires_at is not None and expires_at > self.expires_at:
            raise ValueError("delegation cannot outlive parent principal")
        return AgentPrincipal(
            principal_id=principal_id,
            workspace_id=self.workspace_id,
            roles=roles_set,
            allowed_actions=actions,
            allowed_resources=resources,
            policy_version=self.policy_version,
            expires_at=expires_at,
            delegator_id=self.principal_id,
            delegation_id=delegation_id,
        )

    def audit_context(self) -> Mapping[str, Any]:
        return {
            "principal_id_hash": self.principal_id_hash,
            "roles": sorted(self.roles),
            "policy_version": self.policy_version,
            "delegated": bool(self.delegator_id),
            "delegation_id": self.delegation_id,
        }
