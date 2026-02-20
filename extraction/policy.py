"""
Runtime policy helpers for agent execution.

Design notes:
- MVP is single-tenant, but all runtime calls must carry workspace_id.
- Authorization in hot path is app-level RBAC/ABAC.
- owlready2-style ontology reasoning is intentionally excluded from hot path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Set


_WORKSPACE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class RuntimePolicyEngine:
    """Simple runtime policy engine for MVP."""

    def __init__(self):
        self._role_permissions: Dict[str, Set[str]] = {
            "admin": {
                "run_agent", "run_debate", "read_databases", "read_agents",
                "infer_rules", "validate_rules", "assess_rules", "manage_rule_profiles", "export_rules",
                "manage_indexes", "run_platform", "ingest_raw",
            },
            "user": {
                "run_agent", "run_debate", "read_databases", "read_agents",
                "infer_rules", "validate_rules", "assess_rules", "manage_rule_profiles", "export_rules",
                "manage_indexes", "run_platform", "ingest_raw",
            },
            "viewer": {"read_databases", "read_agents"},
        }

    def validate_workspace_id(self, workspace_id: str) -> PolicyDecision:
        if not workspace_id:
            return PolicyDecision(False, "workspace_id is required")
        if not _WORKSPACE_RE.match(workspace_id):
            return PolicyDecision(False, "invalid workspace_id format")
        return PolicyDecision(True, "ok")

    def authorize(self, role: str, action: str, workspace_id: str) -> PolicyDecision:
        ws = self.validate_workspace_id(workspace_id)
        if not ws.allowed:
            return ws
        allowed_actions = self._role_permissions.get(role, set())
        if action not in allowed_actions:
            return PolicyDecision(False, f"role '{role}' not allowed for action '{action}'")
        return PolicyDecision(True, "ok")


def require_runtime_permission(role: str, action: str, workspace_id: str) -> None:
    decision = RuntimePolicyEngine().authorize(role=role, action=action, workspace_id=workspace_id)
    if not decision.allowed:
        raise PermissionError(decision.reason)


def run_offline_ontology_reasoning(*_: object, **__: object) -> None:
    """
    Placeholder entrypoint for offline ontology reasoning (e.g. owlready2).

    This must not be used in online request handling paths.
    """
    return None
