import pytest

from policy import RuntimePolicyEngine, require_runtime_permission, run_offline_ontology_reasoning


def test_workspace_id_validation():
    engine = RuntimePolicyEngine()
    assert engine.validate_workspace_id("default").allowed is True
    assert engine.validate_workspace_id("ws_01").allowed is True
    assert engine.validate_workspace_id("1bad").allowed is False
    assert engine.validate_workspace_id("").allowed is False


def test_authorize_runtime_action():
    engine = RuntimePolicyEngine()
    allowed = engine.authorize(role="user", action="run_agent", workspace_id="default")
    allowed_manage_index = engine.authorize(role="user", action="manage_indexes", workspace_id="default")
    allowed_platform = engine.authorize(role="user", action="run_platform", workspace_id="default")
    denied = engine.authorize(role="viewer", action="run_debate", workspace_id="default")

    assert allowed.allowed is True
    assert allowed_manage_index.allowed is True
    assert allowed_platform.allowed is True
    assert denied.allowed is False


def test_require_runtime_permission_raises():
    with pytest.raises(PermissionError):
        require_runtime_permission(role="viewer", action="run_debate", workspace_id="default")


def test_offline_reasoning_placeholder_noop():
    # Must be callable and side-effect free in current implementation.
    assert run_offline_ontology_reasoning() is None
