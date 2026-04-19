from __future__ import annotations

import textwrap

import pytest

from seocho import AgentDesignSpec, NodeDef, Ontology, P, RelDef, Seocho, load_agent_design_spec


class _DummyGraphStore:
    pass


class _DummyLLM:
    pass


def _ontology() -> Ontology:
    return Ontology(
        name="finance_design_demo",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


def test_agent_design_requires_ontology_section() -> None:
    with pytest.raises(ValueError, match="requires an 'ontology' section"):
        AgentDesignSpec.from_dict(
            {
                "name": "missing-ontology",
                "pattern": "planning_multi_agent",
            }
        )


def test_agent_design_requires_ontology_binding_reference() -> None:
    with pytest.raises(ValueError, match="must declare an ontology binding"):
        AgentDesignSpec.from_dict(
            {
                "name": "missing-binding",
                "pattern": "planning_multi_agent",
                "ontology": {},
            }
        )


def test_agent_design_pattern_defaults_compile_to_agent_config() -> None:
    spec = AgentDesignSpec.from_dict(
        {
            "name": "planning-finance",
            "pattern": "planning_multi_agent",
            "ontology": {"profile": "finance-core"},
        }
    )

    config = spec.to_agent_config()

    assert config.execution_mode == "supervisor"
    assert config.handoff is True
    assert config.reasoning_mode is True
    assert config.query_strategy == "template"
    assert config.answer_style == "evidence"
    assert config.routing_policy is not None
    assert config.extra["agent_design_pattern"] == "planning_multi_agent"


def test_agent_design_yaml_loader_reads_examples(tmp_path) -> None:
    path = tmp_path / "agent-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: reflection-finance
            pattern: reflection_chain
            description: Finance QA with self-checking repair.
            ontology:
              required: true
              profile: finance-core
            agent:
              reasoning_mode: true
              repair_budget: 4
            query:
              answer_style: evidence
            indexing:
              validation_on_fail: retry
            tools:
              - graph_query
              - finance_table_lookup
            """
        ).strip(),
        encoding="utf-8",
    )

    spec = load_agent_design_spec(path)

    assert spec.name == "reflection-finance"
    assert spec.ontology.resolved_profile() == "finance-core"
    assert spec.tools == ("graph_query", "finance_table_lookup")
    assert spec.to_agent_config().repair_budget == 4


def test_seocho_from_agent_design_applies_agent_config_and_ontology_profile(tmp_path) -> None:
    path = tmp_path / "agent-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: memory-tool-use-finance
            pattern: memory_tool_use
            ontology:
              required: true
              profile: finance-session
            query:
              answer_style: concise
            """
        ).strip(),
        encoding="utf-8",
    )

    client = Seocho.from_agent_design(
        path,
        ontology=_ontology(),
        graph_store=_DummyGraphStore(),
        llm=_DummyLLM(),
        workspace_id="agent-design-test",
    )
    try:
        assert client.ontology_profile == "finance-session"
        assert client.agent_config.execution_mode == "agent"
        assert client.agent_config.answer_style == "concise"
    finally:
        client.close()


def test_seocho_from_agent_design_requires_local_ontology(tmp_path) -> None:
    path = tmp_path / "agent-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: planning-finance
            pattern: planning_multi_agent
            ontology:
              required: true
              profile: finance-core
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="need an ontology object"):
        Seocho.from_agent_design(path)
