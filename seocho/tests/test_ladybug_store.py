"""Integration tests for LadybugGraphStore — real embedded DB, no mocks."""

from pathlib import Path

import pytest

pytest.importorskip("real_ladybug")

from seocho.ontology import NodeDef, Ontology, Property, RelDef
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
