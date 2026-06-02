from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_context import compile_ontology_context
from seocho.semantic_package import compile_semantic_package, select_semantic_packages


def _ontology(version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        package_id="company-finance",
        version=version,
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


def test_compile_semantic_package_is_stable_for_same_ontology_context() -> None:
    ontology_context = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )

    first = compile_semantic_package(
        ontology_context,
        graph_id="finance-graph",
        database="kgnormal",
    )
    second = compile_semantic_package(
        ontology_context,
        graph_id="finance-graph",
        database="kgnormal",
    )

    assert first.package_id == second.package_id
    assert first.package_hash == second.package_hash
    assert first.ontology_id == "company-finance"
    assert first.ontology_profile == "finder-financials"
    assert first.graph_id == "finance-graph"
    assert first.database == "kgnormal"
    assert "financial_metric_lookup" in first.deterministic_intents


def test_select_semantic_packages_prefers_runtime_ontology_context() -> None:
    ontology_context = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )

    selection = select_semantic_packages(
        databases=["kgnormal"],
        workspace_id="acme",
        ontology_contexts={"finance-graph": ontology_context},
        constraint_slices={
            "kgnormal": {
                "graph_id": "finance-graph",
                "ontology_id": "company-finance",
                "vocabulary_profile": "vocabulary.v2",
                "artifact_ids": ["sa_finance"],
                "allowed_labels": ["Company"],
                "allowed_relationship_types": ["REPORTED"],
            }
        },
    )
    payload = selection.to_dict()

    assert payload["source"] == "ontology_context"
    assert payload["package_id"].startswith("semantic-selection:")
    assert payload["packages_by_database"]["kgnormal"]["source"] == "ontology_context"
    assert payload["packages_by_database"]["kgnormal"]["artifact_ids"] == ["sa_finance"]


def test_select_semantic_packages_falls_back_to_constraint_slice() -> None:
    selection = select_semantic_packages(
        databases=["kgfibo"],
        workspace_id="default",
        constraint_slices={
            "kgfibo": {
                "graph_id": "fibo-graph",
                "database": "kgfibo",
                "ontology_id": "fibo",
                "vocabulary_profile": "vocabulary.v2",
                "artifact_ids": ["sa_fibo"],
                "allowed_labels": ["Company", "LegalEntity"],
                "allowed_relationship_types": ["USES"],
                "ontology_candidate": {"ontology_name": "fibo"},
                "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
            }
        },
    )
    payload = selection.to_dict()

    assert payload["source"] == "constraint_slice"
    assert payload["packages_by_database"]["kgfibo"]["source"] == "constraint_slice"
    assert payload["packages_by_database"]["kgfibo"]["ontology_id"] == "fibo"
    assert payload["packages_by_database"]["kgfibo"]["entity_types"] == ["Company", "LegalEntity"]
