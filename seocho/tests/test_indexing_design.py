from __future__ import annotations

import textwrap

import pytest

from seocho import (
    IndexingDesignSpec,
    NodeDef,
    Ontology,
    P,
    RelDef,
    Seocho,
    load_indexing_design_spec,
)


class _DummyGraphStore:
    pass


class _DummyLLM:
    pass


def _ontology() -> Ontology:
    return Ontology(
        name="finance_indexing_demo",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str), "year": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


def test_indexing_design_requires_ontology_section() -> None:
    with pytest.raises(ValueError, match="requires an 'ontology' section"):
        IndexingDesignSpec.from_dict(
            {
                "name": "missing-ontology",
                "graph_model": "lpg",
                "storage_target": "ladybug",
            }
        )


def test_indexing_design_rdf_requires_materialization_policy() -> None:
    with pytest.raises(ValueError, match="materialization.rdf_mode"):
        IndexingDesignSpec.from_dict(
            {
                "name": "rdf-missing-materialization",
                "graph_model": "rdf",
                "storage_target": "neo4j",
                "ontology": {"profile": "finance-core"},
            }
        )


def test_indexing_design_yaml_loader_reads_examples(tmp_path) -> None:
    path = tmp_path / "indexing-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: hybrid-finance
            graph_model: hybrid
            storage_target: neo4j
            description: Hybrid finance indexing with deductive expansion.
            ontology:
              required: true
              profile: finance-core
            ingestion:
              extraction_strategy: domain
              linking_strategy: llm
              validation_on_fail: reject
              inference_mode: deductive
            materialization:
              rdf_mode: neo4j_labels
              metric_model: node
              provenance_mode: full
            reasoning_cycle:
              enabled: true
              anomaly_sources:
                - shacl_violation
                - unsupported_answer
              abduction:
                mode: candidate_only
              deduction:
                require_testable_predictions: true
              induction:
                require_support_assessment: true
            constraints:
              require_workspace_id: true
            """
        ).strip(),
        encoding="utf-8",
    )

    spec = load_indexing_design_spec(path)

    assert spec.name == "hybrid-finance"
    assert spec.graph_model == "hybrid"
    assert spec.default_strict_validation() is True
    assert spec.indexing_metadata()["indexing_design"]["inference_mode"] == "deductive"
    assert spec.reasoning_cycle_enabled() is True
    assert spec.indexing_metadata()["indexing_design"]["reasoning_cycle"]["abduction"]["mode"] == "candidate_only"


def test_indexing_design_materializes_ontology_graph_model() -> None:
    spec = IndexingDesignSpec.from_dict(
        {
            "name": "rdf-governed-finance",
            "graph_model": "rdf",
            "storage_target": "neo4j",
            "ontology": {"profile": "finance-governed"},
            "materialization": {"rdf_mode": "neo4j_labels"},
        }
    )

    materialized = spec.materialize_ontology(_ontology())

    assert materialized.graph_model == "rdf"
    assert materialized.nodes.keys() == _ontology().nodes.keys()
    assert materialized.relationships.keys() == _ontology().relationships.keys()


def test_seocho_from_indexing_design_applies_defaults_and_profile(tmp_path) -> None:
    path = tmp_path / "indexing-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: lpg-finance-fast
            graph_model: lpg
            storage_target: ladybug
            ontology:
              required: true
              profile: finance-fast
            ingestion:
              validation_on_fail: reject
              inference_mode: base
            materialization:
              metric_model: property
              provenance_mode: source
            reasoning_cycle:
              enabled: true
              anomaly_sources:
                - shacl_violation
            """
        ).strip(),
        encoding="utf-8",
    )

    client = Seocho.from_indexing_design(
        path,
        ontology=_ontology(),
        graph_store=_DummyGraphStore(),
        llm=_DummyLLM(),
        workspace_id="finance-indexing-test",
    )
    try:
        assert client.ontology_profile == "finance-fast"
        assert client.ontology.graph_model == "lpg"
        defaults = client._resolve_indexing_design_add_kwargs(
            metadata={"source": "finder"},
            strict_validation=False,
        )
        assert defaults["strict_validation"] is True
        assert defaults["metadata"]["source"] == "finder"
        assert defaults["metadata"]["indexing_design"]["name"] == "lpg-finance-fast"
        assert defaults["metadata"]["indexing_design"]["materialization"]["provenance_mode"] == "source"
        assert defaults["metadata"]["indexing_design"]["reasoning_cycle"]["enabled"] is True
        assert client.extraction_prompt is not None
        assert "Property graph rules" in client.extraction_prompt.system
        assert "candidate-only" in client.extraction_prompt.system
    finally:
        client.close()


def test_seocho_from_indexing_design_requires_graph_for_neo4j_target(tmp_path) -> None:
    path = tmp_path / "indexing-design.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: rdf-finance
            graph_model: rdf
            storage_target: neo4j
            ontology:
              required: true
              profile: finance-core
            materialization:
              rdf_mode: neo4j_labels
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="require graph='bolt://"):
        Seocho.from_indexing_design(path, ontology=_ontology(), llm="openai/gpt-4o", workspace_id="finance-core")
