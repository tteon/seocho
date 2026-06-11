import pytest

from runtime.identity import ANONYMOUS, Principal
from runtime.policy import (
    RuntimePolicyEngine,
    require_runtime_permission,
    run_offline_ontology_reasoning,
)


def test_workspace_id_validation():
    engine = RuntimePolicyEngine()
    assert engine.validate_workspace_id("default").allowed is True
    assert engine.validate_workspace_id("ws_01").allowed is True
    assert engine.validate_workspace_id("1bad").allowed is False
    assert engine.validate_workspace_id("").allowed is False


def test_authorize_runtime_action():
    engine = RuntimePolicyEngine()
    assert engine.authorize(role="user", action="run_agent", workspace_id="default").allowed
    assert engine.authorize(role="user", action="manage_indexes", workspace_id="default").allowed
    assert engine.authorize(role="user", action="run_platform", workspace_id="default").allowed
    assert engine.authorize(role="user", action="assess_rules", workspace_id="default").allowed
    assert engine.authorize(role="user", action="manage_semantic_artifacts", workspace_id="default").allowed
    assert engine.authorize(role="user", action="manage_memories", workspace_id="default").allowed
    # viewer is genuinely read-only
    assert engine.authorize(role="viewer", action="run_debate", workspace_id="default").allowed is False
    assert engine.authorize(role="viewer", action="read_databases", workspace_id="default").allowed


def test_admin_is_strict_superset_of_user():
    engine = RuntimePolicyEngine()
    # admin-only action denied for user, allowed for admin
    assert engine.authorize(role="user", action="manage_tenants", workspace_id="default").allowed is False
    assert engine.authorize(role="admin", action="manage_tenants", workspace_id="default").allowed
    # everything a user can do, admin can do
    assert engine.authorize(role="admin", action="run_agent", workspace_id="default").allowed


def test_workspace_ownership_blocks_cross_tenant():
    engine = RuntimePolicyEngine()
    # a user scoped to ws "acme" may act on acme...
    assert engine.authorize(
        role="user", action="run_agent", workspace_id="acme", principal_workspace="acme"
    ).allowed
    # ...but not on another workspace (IDOR closed)
    assert engine.authorize(
        role="user", action="run_agent", workspace_id="other", principal_workspace="acme"
    ).allowed is False
    # admin is the cross-workspace operator and is exempt
    assert engine.authorize(
        role="admin", action="run_agent", workspace_id="other", principal_workspace="acme"
    ).allowed
    # unconstrained principal (workspace=None) may act on any workspace
    assert engine.authorize(
        role="user", action="run_agent", workspace_id="other", principal_workspace=None
    ).allowed


def test_require_permission_anonymous_does_not_enforce():
    # Auth disabled (anonymous, authenticated=False): role/ownership NOT enforced
    # — reproduces pre-auth behavior. A viewer-denied action does not raise.
    require_runtime_permission(action="run_debate", workspace_id="default", principal=ANONYMOUS)


def test_require_permission_anonymous_still_validates_workspace_format():
    with pytest.raises(PermissionError):
        require_runtime_permission(action="run_agent", workspace_id="1invalid", principal=ANONYMOUS)


def test_require_permission_authenticated_viewer_denied():
    viewer = Principal(subject="v", role="viewer", workspace_id=None, authenticated=True)
    with pytest.raises(PermissionError):
        require_runtime_permission(action="run_debate", workspace_id="default", principal=viewer)


def test_require_permission_authenticated_user_allowed():
    user = Principal(subject="u", role="user", workspace_id=None, authenticated=True)
    require_runtime_permission(action="run_agent", workspace_id="default", principal=user)


def test_require_permission_workspace_ownership_enforced():
    scoped = Principal(subject="u", role="user", workspace_id="acme", authenticated=True)
    # own workspace: ok
    require_runtime_permission(action="run_agent", workspace_id="acme", principal=scoped)
    # cross-workspace: blocked
    with pytest.raises(PermissionError):
        require_runtime_permission(action="run_agent", workspace_id="other", principal=scoped)


def test_offline_reasoning_placeholder_noop():
    assert run_offline_ontology_reasoning() is None
