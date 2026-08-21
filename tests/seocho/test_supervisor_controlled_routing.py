"""The Supervisor routes queries to the controlled agent by default (ADR-0217).

The autonomous multi-tool query loop did not converge with a hosted reasoning
model (ADR-0215). #607 shipped a controlled query agent that converges; this
makes the Supervisor route to it by default so the proven path is the product
default. `controlled_query=False` preserves the tiered autonomous route.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef


def _ontology() -> Ontology:
    return Ontology(
        name="sup",
        nodes={"Entity": NodeDef(properties={"name": P(str, unique=True)}, identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity", description="")},
    )


class _LLM:
    provider = "mara"
    def to_agents_sdk_model(self, *, model=None):
        return "mara/model"


def test_supervisor_builds_and_routes_to_query_and_indexing():
    pytest.importorskip("agents")
    from seocho.agent.factory import create_supervisor_agent

    sup = create_supervisor_agent(ontology=_ontology(), graph_store=object(), llm=_LLM())
    names = {getattr(h, "agent_name", None) for h in sup.handoffs}
    assert names == {"IndexingAgent", "QueryAgent"}, names


def test_controlled_query_is_the_default():
    """The default path is the controlled query agent (single deterministic tool)."""
    pytest.importorskip("agents")
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "agent" / "factory.py").read_text()
    sup_block = src[src.index("def create_supervisor_agent"):]
    assert "controlled_query: bool = True" in sup_block, "controlled routing must default on"
    assert "create_controlled_query_agent(" in sup_block, "supervisor must use the controlled agent"


def test_tiered_route_still_available():
    pytest.importorskip("agents")
    from seocho.agent.factory import create_supervisor_agent

    # controlled_query=False must still build (the tiered autonomous route).
    sup = create_supervisor_agent(
        ontology=_ontology(), graph_store=object(), llm=_LLM(), controlled_query=False)
    assert len(sup.handoffs) == 2
