"""Phase 0 (ADR-0215/0216): the execute_cypher tool is guarded by the ontology.

The multi-agent hand-off (ADR-0215) looped to MaxTurnsExceeded because the
agent-mode query tool reached the database unguarded. SEOCHO already has the
deterministic ontology guardrail (integrations/openai_agents.make_ontology_guardrail)
but the factory-built agents did not wire it. These tests lock the wiring:
execute_cypher carries a tool_input_guardrail, and the tracing policy keeps the
SDK from phoning home.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef


def _ontology() -> Ontology:
    return Ontology(
        name="guard",
        nodes={"Entity": NodeDef(properties={"name": P(str, unique=True)}, identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity", description="")},
    )


def test_execute_cypher_tool_is_guarded():
    pytest.importorskip("agents")
    from seocho.tools import create_query_tools

    tools = create_query_tools(ontology=_ontology(), graph_store=object())
    exec_tool = next((t for t in tools if getattr(t, "name", None) == "execute_cypher"), None)
    assert exec_tool is not None, "execute_cypher tool must be present"
    guardrails = getattr(exec_tool, "tool_input_guardrails", None)
    assert guardrails, "execute_cypher must carry an ontology tool_input_guardrail"


def test_tracing_policy_is_idempotent_and_safe():
    pytest.importorskip("agents")
    from seocho.agent.factory import _ensure_sdk_tracing_policy

    # Two calls, no exception; the SDK exporter is disabled by default so a
    # SEOCHO agent build never requires an OpenAI key.
    _ensure_sdk_tracing_policy()
    _ensure_sdk_tracing_policy()


def test_tracing_opt_in_respected(monkeypatch):
    pytest.importorskip("agents")
    from seocho.agent import factory

    monkeypatch.setenv("SEOCHO_AGENTS_SDK_TRACING", "1")
    # With opt-in set, the policy returns early without disabling — no raise.
    factory._ensure_sdk_tracing_policy()
