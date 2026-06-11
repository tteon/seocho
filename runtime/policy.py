"""
Runtime policy: role-based authorization + workspace-ownership enforcement.

Design notes:
- ``workspace_id`` must accompany every runtime call (format-validated).
- The effective role comes from the **authenticated principal**
  (:mod:`runtime.identity`), not from the caller. When authentication is
  disabled (``SEOCHO_AUTH_MODE=none`` → anonymous, ``authenticated=False``),
  ``require_runtime_permission`` only validates the ``workspace_id`` format and
  does not enforce role/ownership — this reproduces the pre-auth behaviour, so
  existing callers and tests are unaffected.
- When the principal is authenticated, two checks apply: (1) the role must be
  permitted for the action, and (2) **workspace ownership** — a principal
  scoped to a workspace may only act on that workspace; ``admin`` is the
  cross-workspace operator and is exempt. (2) closes the IDOR class where any
  caller could pass any ``workspace_id``.
- owlready2-style ontology reasoning stays out of the hot path.

The permission matrix below is deliberately **behaviour-compatible**: the
``user`` set keeps every action that was previously gated as the hardcoded
"user", so turning auth on does not silently lock out existing flows. ``admin``
is a strict superset; ``viewer`` is genuinely read-only. Tightening governance
actions (rule-profile/export/semantic-artifact management) to admin-only is a
recommended follow-up that needs product input, so it is NOT baked in here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Set

from runtime.audit import record_access
from runtime.identity import Principal, get_principal


_WORKSPACE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")

# Actions that were previously gated as the hardcoded "user" role. Keeping the
# user set equal to this preserves behaviour when auth is enabled.
_OPERATIONAL: Set[str] = {
    "run_agent", "run_debate", "read_databases", "read_agents",
    "infer_rules", "validate_rules", "assess_rules", "manage_rule_profiles", "read_rule_profiles",
    "export_rules", "manage_indexes", "run_platform", "ingest_raw",
    "manage_semantic_artifacts", "read_semantic_artifacts", "manage_memories",
    "read_semantic_runs",
}
# Reserved for cross-tenant / policy operations — admin only.
_ADMIN_ONLY: Set[str] = {"manage_tenants", "manage_policy"}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class RuntimePolicyEngine:
    """Role-based authorization with workspace-ownership enforcement."""

    def __init__(self):
        self._role_permissions: Dict[str, Set[str]] = {
            "admin": _OPERATIONAL | _ADMIN_ONLY,   # strict superset
            "user": set(_OPERATIONAL),
            "viewer": {"read_databases", "read_agents", "read_semantic_runs", "read_rule_profiles", "read_semantic_artifacts"},
        }

    def validate_workspace_id(self, workspace_id: str) -> PolicyDecision:
        if not workspace_id:
            return PolicyDecision(False, "workspace_id is required")
        if not _WORKSPACE_RE.match(workspace_id):
            return PolicyDecision(False, "invalid workspace_id format")
        return PolicyDecision(True, "ok")

    def authorize(
        self,
        role: str,
        action: str,
        workspace_id: str,
        *,
        principal_workspace: Optional[str] = None,
    ) -> PolicyDecision:
        ws = self.validate_workspace_id(workspace_id)
        if not ws.allowed:
            return ws
        if action not in self._role_permissions.get(role, set()):
            return PolicyDecision(False, f"role '{role}' not allowed for action '{action}'")
        # Workspace ownership: a scoped principal may only act on its own
        # workspace. admin is the cross-workspace operator and is exempt.
        if (
            principal_workspace is not None
            and role != "admin"
            and workspace_id != principal_workspace
        ):
            return PolicyDecision(
                False,
                f"principal scoped to workspace '{principal_workspace}' "
                f"may not act on '{workspace_id}'",
            )
        return PolicyDecision(True, "ok")


def require_runtime_permission(
    action: str,
    workspace_id: str,
    *,
    role: Optional[str] = None,  # legacy/ignored: effective role comes from the principal
    principal: Optional[Principal] = None,
) -> None:
    """Enforce that the current principal may perform ``action`` on ``workspace_id``.

    Raises :class:`PermissionError` (mapped to HTTP 403 by the server) on denial.
    The ``role`` keyword is accepted for backward compatibility with existing
    call sites but is ignored — the effective role is taken from the
    authenticated principal.
    """
    principal = principal if principal is not None else get_principal()
    engine = RuntimePolicyEngine()

    if not principal.authenticated:
        # Auth disabled / anonymous: preserve pre-auth behaviour — validate the
        # workspace_id format only; do not enforce role or ownership.
        decision = engine.validate_workspace_id(workspace_id)
    else:
        decision = engine.authorize(
            role=principal.role,
            action=action,
            workspace_id=workspace_id,
            principal_workspace=principal.workspace_id,
        )

    # Audit every decision at the single enforcement point (no-op unless
    # SEOCHO_AUDIT_LOG_PATH is configured). Auditing must never take down the
    # request path, so record_access swallows its own I/O errors.
    record_access(
        action=action,
        workspace_id=workspace_id,
        allowed=decision.allowed,
        reason=decision.reason,
        principal=principal,
    )

    if not decision.allowed:
        raise PermissionError(decision.reason)


def run_offline_ontology_reasoning(*_: object, **__: object) -> None:
    """
    Placeholder entrypoint for offline ontology reasoning (e.g. owlready2).

    This must not be used in online request handling paths.
    """
    return None
