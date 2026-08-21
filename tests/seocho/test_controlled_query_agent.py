"""Phase 1 (ADR-0217): a controlled query agent driven by ONE deterministic tool.

The autonomous multi-tool query loop did not converge with a hosted reasoning
model (ADR-0215). The spike showed a controlled flow — a single deterministic
`answer_from_graph` tool — converges. These tests lock the wiring: the tool
exists and the controlled agent carries exactly that one tool.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef


def _ontology() -> Ontology:
    return Ontology(
        name="ctrl",
        nodes={"Entity": NodeDef(properties={"name": P(str, unique=True)}, identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity", description="")},
    )


def test_deterministic_query_tool_builds():
    pytest.importorskip("agents")
    from seocho.tools import make_deterministic_query_tool

    tool = make_deterministic_query_tool(
        ontology=_ontology(), graph_store=object(), llm=object(), workspace_id="w")
    assert getattr(tool, "name", None) == "answer_from_graph"


def test_controlled_query_agent_has_single_deterministic_tool():
    pytest.importorskip("agents")
    from seocho.agent.factory import create_controlled_query_agent

    class _LLM:
        provider = "mara"
        def to_agents_sdk_model(self, *, model=None):
            return "mara/model"

    agent = create_controlled_query_agent(
        ontology=_ontology(), graph_store=object(), llm=_LLM(), workspace_id="w")
    names = [getattr(t, "name", None) for t in agent.tools]
    assert names == ["answer_from_graph"], names
