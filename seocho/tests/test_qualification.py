from __future__ import annotations

import pytest

from seocho import (
    CurationDesignSpec,
    EntityCurationPolicy,
    NodeDef,
    Ontology,
    P,
    RelDef,
    Seocho,
)


class _FakeGraphStore:
    def __init__(self) -> None:
        self.write_calls: list[dict] = []

    def write(
        self,
        nodes,
        relationships,
        *,
        database="neo4j",
        workspace_id="default",
        source_id="",
    ):  # noqa: ANN001
        self.write_calls.append(
            {
                "nodes": list(nodes),
                "relationships": list(relationships),
                "database": database,
                "workspace_id": workspace_id,
                "source_id": source_id,
            }
        )
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
            "errors": [],
        }


def test_add_graph_captures_observed_entities_and_projects_canonical_graph(tmp_path) -> None:
    ontology = Ontology(
        name="contracts",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    store = _FakeGraphStore()
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=object(),
        qualification_store_path=str(tmp_path / "qualification.duckdb"),
    )

    memory = client.add_graph(
        {
            "nodes": [
                {"id": "acme_a", "label": "Company", "properties": {"name": "ACME"}},
                {"id": "acme_b", "label": "Company", "properties": {"name": "ACME"}},
            ],
            "relationships": [],
        },
        content="# Overview\n\nACME entered Asia.",
    )

    capture = memory.metadata["qualification_capture"]
    assert capture["observed_entities_recorded"] == 2

    run = client.qualify_graph(database=client.default_database, modes=("text", "graph"))
    assert run.store_backend == "sqlite"
    assert run.case_count == 1

    cases = client.list_curation_cases(run_id=run.run_id)
    assert len(cases) == 1
    preview = client.preview_curation_decision(cases[0].case_id, action="merge")
    assert preview.canonical_entity_id is not None
    assert preview.canonical_entity_id.startswith("canonical_company_acme")

    decision = client.apply_curation_decision(cases[0].case_id, action="merge")
    assert decision.status == "applied"

    projection = client.project_canonical_graph(
        database=client.default_database,
        run_id=run.run_id,
    )
    assert projection.nodes_written == 1
    projected = store.write_calls[-1]
    assert len(projected["nodes"]) == 1
    assert projected["nodes"][0]["id"].startswith("canonical_company_acme")
    assert projected["relationships"] == []


def test_qualification_blocks_merge_when_identity_keys_conflict(tmp_path) -> None:
    ontology = Ontology(
        name="finance",
        nodes={
            "Company": NodeDef(
                properties={
                    "name": P(str, required=True),
                    "ticker": P(str, unique=True),
                }
            )
        },
        relationships={},
    )
    store = _FakeGraphStore()
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=object(),
        qualification_store_path=str(tmp_path / "qualification.duckdb"),
    )

    client.add_graph(
        {
            "nodes": [
                {"id": "acme_nyse", "label": "Company", "properties": {"name": "ACME", "ticker": "ACM"}},
                {"id": "acme_lse", "label": "Company", "properties": {"name": "ACME", "ticker": "ACX"}},
            ],
            "relationships": [],
        }
    )

    design = CurationDesignSpec(
        name="finance-curation",
        entity_policies={
            "Company": EntityCurationPolicy(
                identity_keys=["ticker"],
                fallback_identity_keys=["name"],
            )
        },
    )
    run = client.qualify_graph(
        database=client.default_database,
        curation_design=design,
        modes=("text", "graph"),
    )
    assert run.case_count == 1
    case = client.list_curation_cases(run_id=run.run_id)[0]
    assert case.recommended_action == "keep_separate"
    assert "identity_conflict:ticker" in case.blocked_reasons

    preview = client.preview_curation_decision(case.case_id, action="merge")
    assert "identity_conflict:ticker" in preview.blocked_reasons

    with pytest.raises(ValueError):
        client.apply_curation_decision(case.case_id, action="merge")


def test_projection_keeps_distinct_relation_instances_by_qualifier_hash(tmp_path) -> None:
    ontology = Ontology(
        name="employment",
        nodes={
            "Person": NodeDef(properties={"name": P(str, required=True)}),
            "Company": NodeDef(properties={"name": P(str, required=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(
                source="Person",
                target="Company",
                properties={"since": P(str)},
            )
        },
    )
    store = _FakeGraphStore()
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=object(),
        qualification_store_path=str(tmp_path / "qualification.duckdb"),
    )

    client.add_graph(
        {
            "nodes": [
                {"id": "alice", "label": "Person", "properties": {"name": "Alice"}},
                {"id": "acme", "label": "Company", "properties": {"name": "ACME"}},
            ],
            "relationships": [
                {"source": "alice", "target": "acme", "type": "WORKS_AT", "properties": {"since": "2023"}},
                {"source": "alice", "target": "acme", "type": "WORKS_AT", "properties": {"since": "2024"}},
            ],
        }
    )

    projection = client.project_canonical_graph(database=client.default_database)
    assert projection.relationships_written == 2
    projected = store.write_calls[-1]
    assert len(projected["nodes"]) == 2
    assert len(projected["relationships"]) == 2
    assert {rel["properties"]["since"] for rel in projected["relationships"]} == {"2023", "2024"}


def test_qualification_store_supports_explicit_duckdb_backend_request_when_unavailable(tmp_path) -> None:
    ontology = Ontology(
        name="contracts",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    store = _FakeGraphStore()
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=object(),
        qualification_store_path=str(tmp_path / "qualification.db"),
        qualification_store_backend="sqlite",
    )

    client.add_graph(
        {
            "nodes": [{"id": "acme", "label": "Company", "properties": {"name": "ACME"}}],
            "relationships": [],
        }
    )

    run = client.qualify_graph(database=client.default_database)
    assert run.store_backend == "sqlite"
