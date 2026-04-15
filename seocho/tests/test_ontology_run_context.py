from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.models import GraphTarget
from seocho.ontology_context import compile_ontology_context
from seocho.ontology_run_context import (
    OntologyEvidenceState,
    OntologyPolicyDecision,
    OntologyRunContext,
    build_local_ontology_run_context,
    build_runtime_ontology_run_context,
)


def _ontology() -> Ontology:
    return Ontology(
        name="finance",
        package_id="company-finance",
        version="1.0.0",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


def test_build_local_ontology_run_context_from_compiled_context() -> None:
    compiled = compile_ontology_context(
        _ontology(),
        workspace_id="acme",
        profile="finder-financials",
    )

    context = build_local_ontology_run_context(
        compiled,
        graph_ids=["finance_kg"],
        database="neo4j",
        user_id="u1",
        agent_id="query-agent",
        session_id="s1",
        turn_id="t1",
        reasoning_mode=True,
        repair_budget=2,
        tool_budget=4,
        evidence_state=OntologyEvidenceState(
            intent_id="financial_metric_lookup",
            required_slots=("company", "metric"),
            filled_slots=("company",),
            missing_slots=("metric",),
            selected_triples=({"subject": "ACME", "predicate": "REPORTED"},),
        ),
    )
    payload = context.to_dict()

    assert context.workspace_id == "acme"
    assert context.ontology_id == "company-finance"
    assert context.ontology_profile == "finder-financials"
    assert context.vocabulary_profile == "finder-financials"
    assert context.ontology_context_hash == compiled.descriptor.context_hash
    assert context.glossary_hash == compiled.descriptor.glossary_hash
    assert context.databases == ("neo4j",)
    assert context.allowed_databases == ("neo4j",)
    assert context.allows_database("neo4j") is True
    assert context.allows_database("other") is False
    assert payload["evidence_state"]["missing_slots"] == ["metric"]
    assert payload["evidence_state"]["selected_triple_count"] == 1
    assert payload["blocked"] is False


def test_build_runtime_ontology_run_context_from_graph_targets() -> None:
    targets = [
        GraphTarget(
            graph_id="finance",
            database="finance_db",
            uri="bolt://neo4j:7687",
            ontology_id="finance-v1",
            vocabulary_profile="finance-glossary",
        )
    ]

    context = build_runtime_ontology_run_context(
        targets,
        workspace_id="acme",
        ontology_profile="runtime-profile",
        reasoning_mode=True,
        repair_budget=1,
    )

    assert context.workspace_id == "acme"
    assert context.graph_ids == ("finance",)
    assert context.databases == ("finance_db",)
    assert context.allowed_databases == ("finance_db",)
    assert context.ontology_id == "finance-v1"
    assert context.ontology_profile == "runtime-profile"
    assert context.vocabulary_profile == "finance-glossary"
    assert context.allows_database("finance_db") is True
    assert context.allows_database("neo4j") is False


def test_runtime_ontology_run_context_marks_mixed_targets() -> None:
    context = OntologyRunContext.from_runtime_graph_targets(
        [
            {
                "graph_id": "g1",
                "database": "db1",
                "ontology_id": "onto-a",
                "vocabulary_profile": "vocab-a",
            },
            {
                "graph_id": "g2",
                "database": "db2",
                "ontology_id": "onto-b",
                "vocabulary_profile": "vocab-b",
            },
        ],
        allowed_databases=["db1"],
        policy_decision=OntologyPolicyDecision.warn(
            "second database is not selected",
            action="query",
        ),
    )

    assert context.graph_ids == ("g1", "g2")
    assert context.databases == ("db1", "db2")
    assert context.allowed_databases == ("db1",)
    assert context.ontology_id == "mixed"
    assert context.vocabulary_profile == "mixed"
    assert context.policy_decision.allowed is True
    assert context.allows_database("db1") is True
    assert context.allows_database("db2") is False


def test_ontology_run_context_round_trips_nested_state() -> None:
    context = OntologyRunContext(
        workspace_id="acme",
        databases=("finance_db",),
        policy_decision=OntologyPolicyDecision.block("unauthorized", action="write"),
        ontology_context_mismatch={"mismatch": True, "warning": "drift"},
        evidence_state=OntologyEvidenceState(
            intent_id="lookup",
            required_slots=("company",),
            missing_slots=("company",),
            abstention_reason="missing company",
        ),
    )

    restored = OntologyRunContext.from_dict(context.to_dict())
    summary = restored.summary()

    assert restored.blocked is True
    assert restored.policy_decision.reason == "unauthorized"
    assert restored.evidence_state.complete is False
    assert restored.ontology_context_mismatch["mismatch"] is True
    assert summary["policy_decision"] == "block"
    assert summary["ontology_mismatch"] is True
    assert summary["missing_evidence_slots"] == ["company"]


def test_public_import_surface_exposes_ontology_run_context() -> None:
    import seocho

    assert seocho.OntologyRunContext is OntologyRunContext
    assert seocho.OntologyPolicyDecision.warn("check").decision == "warn"
