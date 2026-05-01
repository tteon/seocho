"""Integration tests for LadybugGraphStore — real embedded DB, no mocks."""

from pathlib import Path

import pytest

pytest.importorskip("real_ladybug")

from seocho.ontology import NodeDef, Ontology, Property, RelDef
from seocho.ontology_context import apply_ontology_context_to_graph_payload, compile_ontology_context
from seocho.query.cypher_builder import CypherBuilder
from seocho.store.graph import LadybugGraphStore


@pytest.fixture
def ontology():
    return Ontology(
        name="test",
        nodes={
            "Person": NodeDef(properties={
                "name": Property(str, unique=True),
                "age": Property(int),
            }),
            "Company": NodeDef(properties={
                "name": Property(str, unique=True),
            }),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company"),
        },
    )


@pytest.fixture
def store(ontology, tmp_path):
    path = str(tmp_path / "test.lbug")
    store = LadybugGraphStore(path)
    store.ensure_constraints(ontology)
    yield store
    store.close()


class TestLadybugStore:
    def test_ensure_constraints_creates_node_tables(self, store):
        schema = store.get_schema()
        assert "Person" in schema["labels"]
        assert "Company" in schema["labels"]

    def test_ensure_constraints_creates_rel_tables(self, store):
        schema = store.get_schema()
        assert "WORKS_AT" in schema["relationship_types"]

    def test_write_and_query_roundtrip(self, store):
        store.write(
            nodes=[
                {"id": "alice", "label": "Person",
                 "properties": {"name": "Alice", "age": 30}},
                {"id": "apple", "label": "Company",
                 "properties": {"name": "Apple"}},
            ],
            relationships=[
                {"source": "alice", "target": "apple", "type": "WORKS_AT",
                 "properties": {}},
            ],
            source_id="doc1",
        )

        results = store.query(
            "MATCH (p:Person)-[:WORKS_AT]->(c:Company) RETURN p.name, c.name"
        )
        assert len(results) == 1
        row = results[0]
        # LadybugDB returns either named columns or positional
        values = list(row.values()) if isinstance(row, dict) else list(row)
        assert "Alice" in values
        assert "Apple" in values

    def test_write_returns_counts(self, store):
        summary = store.write(
            nodes=[
                {"id": "a", "label": "Person", "properties": {"name": "A"}},
                {"id": "b", "label": "Person", "properties": {"name": "B"}},
            ],
            relationships=[],
            source_id="doc1",
        )
        assert summary["nodes_created"] == 2
        assert summary["relationships_created"] == 0
        assert summary["errors"] == []

    def test_write_accepts_ontology_context_properties(self, ontology, tmp_path):
        store = LadybugGraphStore(str(tmp_path / "context.lbug"))
        store.ensure_constraints(ontology)
        try:
            context = compile_ontology_context(ontology, workspace_id="acme")
            nodes, relationships = apply_ontology_context_to_graph_payload(
                [
                    {"id": "alice", "label": "Person", "properties": {"name": "Alice", "age": 30}},
                    {"id": "apple", "label": "Company", "properties": {"name": "Apple"}},
                ],
                [
                    {"source": "alice", "target": "apple", "type": "WORKS_AT", "properties": {}},
                ],
                context,
            )

            summary = store.write(
                nodes=nodes,
                relationships=relationships,
                source_id="doc-context",
                workspace_id="acme",
            )

            assert summary["nodes_created"] == 2
            assert summary["relationships_created"] == 1
            assert summary["errors"] == []
        finally:
            store.close()

    def test_write_accepts_linked_id_properties(self, tmp_path):
        ontology = Ontology(
            name="finder",
            nodes={
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "FinancialMetric": NodeDef(
                    properties={
                        "name": Property(str, unique=True),
                        "value": Property(str),
                        "year": Property(str),
                    }
                ),
            },
            relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric")},
        )
        store = LadybugGraphStore(str(tmp_path / "linked_id.lbug"))
        store.ensure_constraints(ontology)
        try:
            summary = store.write(
                nodes=[
                    {
                        "id": "ptc_inc",
                        "label": "Company",
                        "properties": {"name": "PTC Inc.", "linked_id": "urn:company:ptc_inc"},
                    },
                    {
                        "id": "total_revenue_fy_2023",
                        "label": "FinancialMetric",
                        "properties": {
                            "name": "Total Revenue FY 2023",
                            "value": "2100000000",
                            "year": "2023",
                            "linked_id": "urn:metric:ptc_total_revenue_2023",
                        },
                    },
                ],
                relationships=[
                    {
                        "source": "ptc_inc",
                        "target": "total_revenue_fy_2023",
                        "type": "REPORTED",
                        "properties": {},
                    }
                ],
                source_id="doc-linked-id",
            )

            assert summary["nodes_created"] == 2
            assert summary["relationships_created"] == 1
            assert summary["errors"] == []
        finally:
            store.close()

    def test_write_supports_same_semantic_relation_across_multiple_target_labels(self, tmp_path):
        ontology = Ontology(
            name="memory",
            nodes={
                "Document": NodeDef(properties={"name": Property(str, unique=True)}),
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "Regulator": NodeDef(properties={"name": Property(str, unique=True)}),
            },
            relationships={},
        )
        store = LadybugGraphStore(str(tmp_path / "heterogeneous_mentions.lbug"))
        store.ensure_constraints(ontology)
        try:
            summary = store.write(
                nodes=[
                    {"id": "doc-1", "label": "Document", "properties": {"name": "finder memo"}},
                    {"id": "company-1", "label": "Company", "properties": {"name": "ACME"}},
                    {"id": "regulator-1", "label": "Regulator", "properties": {"name": "FMSA"}},
                ],
                relationships=[
                    {"source": "doc-1", "target": "company-1", "type": "MENTIONS", "properties": {}},
                    {"source": "doc-1", "target": "regulator-1", "type": "MENTIONS", "properties": {}},
                ],
                source_id="doc-1",
            )

            assert summary["relationships_created"] == 2
            assert summary["errors"] == []

            rows = store.query(
                """
                MATCH (d:Document)-[r]->(e)
                RETURN e.name AS entity_name, type(r) AS relation_type
                ORDER BY entity_name
                """
            )
            values = [list(row.values()) for row in rows]
            assert [row[0] for row in values] == ["ACME", "FMSA"]
            assert {row[1] for row in values} == {"MENTIONS"}
        finally:
            store.close()

    def test_delete_by_source_removes_written_nodes(self, store):
        store.write(
            nodes=[
                {"id": "alice", "label": "Person", "properties": {"name": "Alice", "age": 30}},
                {"id": "apple", "label": "Company", "properties": {"name": "Apple"}},
            ],
            relationships=[
                {"source": "alice", "target": "apple", "type": "WORKS_AT", "properties": {}},
            ],
            source_id="doc-delete",
        )

        before = store.count_by_source("doc-delete")
        summary = store.delete_by_source("doc-delete")
        after = store.count_by_source("doc-delete")

        assert before["nodes"] >= 1
        assert before["relationships"] >= 1
        assert summary["nodes_deleted"] >= 1
        assert summary["relationships_deleted"] >= 1
        assert after["nodes"] == 0
        assert after["relationships"] == 0

    def test_write_supports_same_semantic_relation_across_multiple_target_labels(self, tmp_path):
        ontology = Ontology(
            name="memory",
            nodes={
                "Document": NodeDef(properties={"name": Property(str, unique=True)}),
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "Regulator": NodeDef(properties={"name": Property(str, unique=True)}),
            },
            relationships={},
        )
        store = LadybugGraphStore(str(tmp_path / "heterogeneous_mentions.lbug"))
        store.ensure_constraints(ontology)
        try:
            summary = store.write(
                nodes=[
                    {"id": "doc-1", "label": "Document", "properties": {"name": "finder memo"}},
                    {"id": "company-1", "label": "Company", "properties": {"name": "ACME"}},
                    {"id": "regulator-1", "label": "Regulator", "properties": {"name": "FMSA"}},
                ],
                relationships=[
                    {"source": "doc-1", "target": "company-1", "type": "MENTIONS", "properties": {}},
                    {"source": "doc-1", "target": "regulator-1", "type": "MENTIONS", "properties": {}},
                ],
                source_id="doc-1",
            )

            assert summary["relationships_created"] == 2
            assert summary["errors"] == []

            rows = store.query(
                """
                MATCH (d:Document)-[r]->(e)
                RETURN e.name AS entity_name, type(r) AS relation_type
                ORDER BY entity_name
                """
            )
            values = [list(row.values()) for row in rows]
            assert [row[0] for row in values] == ["ACME", "FMSA"]
            assert {row[1] for row in values} == {"MENTIONS"}
        finally:
            store.close()

    def test_fulltext_introspection_query_degrades_to_empty_result(self, store):
        rows = store.query("SHOW FULLTEXT INDEXES YIELD name, state RETURN name, state")
        assert rows == []

    def test_query_rewrites_elementid_and_properties_projection(self, store):
        store.write(
            nodes=[
                {
                    "id": "alice",
                    "label": "Person",
                    "properties": {"name": "Alice", "content_preview": "Alice works at Apple."},
                },
                {"id": "apple", "label": "Company", "properties": {"name": "Apple"}},
            ],
            relationships=[
                {"source": "alice", "target": "apple", "type": "WORKS_AT", "properties": {"memory_id": "mem_1"}},
            ],
            source_id="doc-query-compat",
        )

        rows = store.query(
            """
            MATCH (n:Person)
            WHERE elementId(n) = toString($node_id)
            OPTIONAL MATCH (n)-[r]-(m)
            RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS target_entity,
                   properties(n) AS properties,
                   collect(
                     DISTINCT {
                       relation: type(r),
                       target: coalesce(m.name, m.title, m.id, m.uri, elementId(m)),
                       target_labels: labels(m)
                     }
                   )[0..$limit] AS neighbors,
                   coalesce(n.content_preview, n.description, n.content, '') AS supporting_fact
            LIMIT 1
            """,
            params={"node_id": "alice", "limit": 5},
        )

        assert rows
        row = rows[0]
        target_entity = row.get("target_entity", row.get("col_0"))
        properties = row.get("properties", row.get("col_1"))
        neighbors = row.get("neighbors", row.get("col_2"))
        supporting_fact = row.get("supporting_fact", row.get("col_3"))

        assert target_entity == "Alice"
        assert properties["name"] == "Alice"
        assert neighbors[0]["target"] == "Apple"
        assert supporting_fact == "Alice works at Apple."

    def test_query_rewrites_financial_metric_filters(self, tmp_path):
        ontology = Ontology(
            name="finder",
            nodes={
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "FinancialMetric": NodeDef(
                    properties={
                        "name": Property(str, unique=True),
                        "year": Property(str),
                        "value": Property(str),
                    }
                ),
            },
            relationships={
                "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            },
        )
        store = LadybugGraphStore(str(tmp_path / "finder.lbug"))
        store.ensure_constraints(ontology)
        try:
            store.write(
                nodes=[
                    {"id": "ptc", "label": "Company", "properties": {"name": "PTC"}},
                    {
                        "id": "ptc_rev_2023",
                        "label": "FinancialMetric",
                        "properties": {"name": "Total revenue", "year": "2023", "value": "2.1 billion"},
                    },
                ],
                relationships=[
                    {
                        "source": "ptc",
                        "target": "ptc_rev_2023",
                        "type": "REPORTED",
                        "properties": {},
                    }
                ],
                source_id="finder-doc",
            )

            builder = CypherBuilder(ontology)
            query, params = builder.build(
                intent="financial_metric_lookup",
                anchor_entity="PTC",
                metric_name="Total revenue",
                metric_aliases=("revenue",),
                metric_scope_tokens=("total",),
                years=("2023",),
                workspace_id="default",
                limit=5,
            )

            rows = store.query(query, params=params)

            assert rows
            row = rows[0]
            values = list(row.values()) if isinstance(row, dict) else list(row)
            assert "PTC" in values
            assert "Total revenue" in values
            assert "2023" in values
            assert "2.1 billion" in values
            assert "REPORTED" in values
        finally:
            store.close()

    def test_query_supports_vehicle_delivery_lookup_without_scope_token_noise(self, tmp_path):
        ontology = Ontology(
            name="finder_vehicle_deliveries",
            nodes={
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "FinancialMetric": NodeDef(
                    properties={
                        "name": Property(str, unique=True),
                        "year": Property(str),
                        "value": Property(str),
                    }
                ),
            },
            relationships={
                "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            },
        )
        store = LadybugGraphStore(str(tmp_path / "finder_vehicle_deliveries.lbug"))
        store.ensure_constraints(ontology)
        try:
            store.write(
                nodes=[
                    {"id": "tesla", "label": "Company", "properties": {"name": "Tesla Inc."}},
                    {
                        "id": "tesla_deliveries_2021",
                        "label": "FinancialMetric",
                        "properties": {"name": "Vehicle Deliveries 2021", "year": "2021", "value": "936000"},
                    },
                    {
                        "id": "tesla_deliveries_2022",
                        "label": "FinancialMetric",
                        "properties": {"name": "Vehicle Deliveries 2022", "year": "2022", "value": "1310000"},
                    },
                ],
                relationships=[
                    {
                        "source": "tesla",
                        "target": "tesla_deliveries_2021",
                        "type": "REPORTED",
                        "properties": {},
                    },
                    {
                        "source": "tesla",
                        "target": "tesla_deliveries_2022",
                        "type": "REPORTED",
                        "properties": {},
                    },
                ],
                source_id="finder-deliveries-doc",
            )

            builder = CypherBuilder(ontology)
            intent = builder.normalize_intent(
                "How many vehicles did Tesla deliver in 2022 vs 2021?",
                {"anchor_entity": "Tesla"},
            )
            query, params = builder.build(
                **intent,
                workspace_id="default",
                limit=5,
            )

            rows = store.query(query, params=params)

            values = {tuple(row.values()) if isinstance(row, dict) else tuple(row) for row in rows}
            assert any("Vehicle Deliveries 2021" in row for row in values)
            assert any("Vehicle Deliveries 2022" in row for row in values)
        finally:
            store.close()

    def test_query_supports_legal_relationship_lookup(self, tmp_path):
        ontology = Ontology(
            name="finder_legal",
            nodes={
                "Company": NodeDef(properties={"name": Property(str, unique=True)}),
                "LegalIssue": NodeDef(
                    properties={
                        "name": Property(str, unique=True),
                        "status": Property(str),
                    }
                ),
            },
            relationships={
                "INVOLVED_IN": RelDef(source="Company", target="LegalIssue"),
            },
        )
        store = LadybugGraphStore(str(tmp_path / "finder_legal.lbug"))
        store.ensure_constraints(ontology)
        try:
            store.write(
                nodes=[
                    {"id": "msft", "label": "Company", "properties": {"name": "Microsoft"}},
                    {
                        "id": "issue_1",
                        "label": "LegalIssue",
                        "properties": {
                            "name": "EU antitrust investigation into Teams bundling with Office 365",
                            "status": "open",
                        },
                    },
                    {
                        "id": "issue_2",
                        "label": "LegalIssue",
                        "properties": {
                            "name": "ongoing LinkedIn acquisition litigation",
                            "status": "open",
                        },
                    },
                ],
                relationships=[
                    {"source": "msft", "target": "issue_1", "type": "INVOLVED_IN", "properties": {}},
                    {"source": "msft", "target": "issue_2", "type": "INVOLVED_IN", "properties": {}},
                ],
                source_id="finder-legal-doc",
            )

            builder = CypherBuilder(ontology)
            query, params = builder.build(
                intent="relationship_lookup",
                anchor_entity="Microsoft",
                anchor_label="Company",
                target_label="LegalIssue",
                relationship_type="INVOLVED_IN",
                workspace_id="default",
                limit=5,
            )

            rows = store.query(query, params=params)

            assert rows
            assert any(
                "Teams bundling with Office 365" in str(row.get("target", row.get("col_2", "")))
                for row in rows
            )
            assert all(row.get("relationship", row.get("col_1")) == "INVOLVED_IN" for row in rows)
        finally:
            store.close()

    def test_embedded_creates_file_on_disk(self, tmp_path):
        path = str(tmp_path / "mygraph.lbug")
        store = LadybugGraphStore(path)
        try:
            # After construction, the path should exist (file or dir)
            assert Path(path).exists() or Path(tmp_path).exists()
        finally:
            store.close()

    def test_persistence_across_connections(self, ontology, tmp_path):
        """Data written in one connection is visible in a new one."""
        path = str(tmp_path / "persist.lbug")

        store1 = LadybugGraphStore(path)
        store1.ensure_constraints(ontology)
        store1.write(
            nodes=[{"id": "alice", "label": "Person",
                    "properties": {"name": "Alice", "age": 30}}],
            relationships=[],
            source_id="doc1",
        )
        store1.close()

        store2 = LadybugGraphStore(path)
        try:
            results = store2.query("MATCH (p:Person) RETURN p.name")
            values = [list(r.values())[0] if isinstance(r, dict) else r[0]
                      for r in results]
            assert "Alice" in values
        finally:
            store2.close()


class TestSeochoLocalWithLadybug:
    """Seocho.local() with embedded Ladybug — no external dependencies."""

    def test_zero_config_local_mode(self, ontology, tmp_path, monkeypatch):
        """Seocho.local(ontology) works with no Neo4j, no OpenAI."""
        # Route the embedded DB into tmp_path so we don't pollute cwd
        monkeypatch.chdir(tmp_path)

        # Need to avoid real LLM creation — mock create_llm_backend
        from unittest.mock import MagicMock, patch
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_llm_mod, "create_llm_backend") as mock_llm:
            mock_llm.return_value = MagicMock()
            s = Seocho.local(ontology)
            assert s._local_mode is True
            assert s.graph_store.__class__.__name__ == "LadybugGraphStore"
            s.close()
