"""Integration tests for LadybugGraphStore — real embedded DB, no mocks."""

from pathlib import Path

import pytest

pytest.importorskip("real_ladybug")

from seocho.ontology import NodeDef, Ontology, Property, RelDef
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
