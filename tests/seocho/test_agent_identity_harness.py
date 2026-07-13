from datetime import datetime, timedelta, timezone

import pytest

from seocho.agent.harness import HarnessManifest, HarnessPromotionGate, RubricScore
from seocho.agent.identity import AgentPrincipal
from seocho.agent.tool_boundary import ToolBoundaryGuard
from seocho.query.sdcr import Evidence


def _principal() -> AgentPrincipal:
    return AgentPrincipal(
        principal_id="agent-authority",
        workspace_id="workspace-a",
        roles=frozenset({"reader", "retriever"}),
        allowed_actions=frozenset({"tool.invoke"}),
        allowed_resources=frozenset({"memory.current", "graph.retrieve"}),
        policy_version="policy-7",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_delegation_cannot_expand_parent_authority() -> None:
    principal = _principal()
    child = principal.delegate(
        principal_id="retrieval-subagent",
        delegation_id="grant-1",
        roles={"retriever"},
        allowed_actions={"tool.invoke"},
        allowed_resources={"graph.retrieve"},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert child.authorize(
        action="tool.invoke", resource="graph.retrieve", workspace_id="workspace-a"
    ).allowed
    assert not child.authorize(
        action="tool.invoke", resource="memory.current", workspace_id="workspace-a"
    ).allowed
    with pytest.raises(ValueError, match="resources exceed"):
        principal.delegate(
            principal_id="bad",
            delegation_id="grant-2",
            roles={"retriever"},
            allowed_actions={"tool.invoke"},
            allowed_resources={"admin.delete"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )


def test_tool_boundary_authorizes_input_and_filters_output() -> None:
    guard = ToolBoundaryGuard(max_input_bytes=128)
    decision, receipt = guard.authorize_input(
        principal=_principal(),
        workspace_id="workspace-a",
        tool_id="graph.retrieve",
        arguments={"memory_ref": "opaque-1"},
    )
    assert decision.allowed and receipt.allowed
    safe, output = guard.filter_output(
        principal=_principal(),
        tool_id="graph.retrieve",
        evidence=(
            Evidence("r1", "graph.retrieve", "state", "ok"),
            Evidence("r2", "graph.retrieve", "wallet", "secret", protected=True),
        ),
    )
    assert [item.source_id for item in safe] == ["r1"]
    assert output.protected_items_removed == 1


def test_harness_gate_requires_complete_critical_rubrics() -> None:
    baseline = HarnessManifest("1", "agents-sdk", "mara", "p1", "o1", "g1", "r1")
    candidate = HarnessManifest("2", "agents-sdk", "mara", "p2", "o1", "g1", "r2")
    held = HarnessPromotionGate().evaluate(
        baseline=baseline,
        candidate=candidate,
        scores=(
            RubricScore("grounded", 0.99, 0.95),
            RubricScore("no_disclosure", 0.0, 1.0, critical=True),
        ),
    )
    assert not held.allowed
    assert held.status == "hold_candidate"
    assert held.failed_rubrics == ("no_disclosure",)
