"""Tests for Ontology.apply_migration() and Seocho.migrate()."""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef


# ---------------------------------------------------------------------------
# Fake GraphStore that records executed writes
# ---------------------------------------------------------------------------

class FakeGraphStore:
    """Minimal graph store that records execute_write calls."""

    def __init__(self):
        self.writes: List[Dict[str, Any]] = []
        self.constraints_applied: List[Any] = []

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        self.writes.append({"cypher": cypher, "params": params, "database": database})
        return {"nodes_affected": 0, "relationships_affected": 0, "properties_set": 0}

    def ensure_constraints(self, ontology, *, database="neo4j"):
        self.constraints_applied.append({"ontology": ontology.name, "database": database})
        return {"success": 1, "errors": []}

    # Stubs for GraphStore ABC compliance
    def write(self, *a, **kw):
        pass

    def query(self, *a, **kw):
        return []

    def get_schema(self, **kw):
        return {"labels": [], "relationship_types": [], "property_keys": []}

    def delete_by_source(self, *a, **kw):
        return {"nodes_deleted": 0, "relationships_deleted": 0}

    def count_by_source(self, *a, **kw):
        return {"nodes": 0, "relationships": 0}

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def v1_ontology():
    return Ontology(
        name="test",
        version="1.0",
        nodes={
            "Person": NodeDef(
                properties={"name": P(str), "age": P(int)},
            ),
            "OldEntity": NodeDef(
                properties={"value": P(str)},
            ),
        },
        relationships={
            "KNOWS": RelDef(source="Person", target="Person"),
            "OLD_REL": RelDef(source="Person", target="OldEntity"),
        },
    )


@pytest.fixture
def v2_ontology():
    return Ontology(
        name="test",
        version="2.0",
        nodes={
            "Person": NodeDef(
                properties={"name": P(str), "email": P(str)},  # age removed, email added
            ),
            "Company": NodeDef(  # new entity type
                properties={"name": P(str)},
            ),
        },
        relationships={
            "KNOWS": RelDef(source="Person", target="Person"),  # unchanged
            "WORKS_AT": RelDef(source="Person", target="Company"),  # new
        },
    )


# ---------------------------------------------------------------------------
# Tests: migration_plan
# ---------------------------------------------------------------------------

class TestMigrationPlan:
    def test_detects_additions(self, v1_ontology, v2_ontology):
        plan = v1_ontology.migration_plan(v2_ontology)
        additions = plan["additions"]
        added_types = [(a["type"], a.get("label") or a.get("relationship")) for a in additions]
        assert ("node", "Company") in added_types
        assert ("relationship", "WORKS_AT") in added_types
        assert ("property", "email") in [(a["type"], a.get("property")) for a in additions]

    def test_detects_removals(self, v1_ontology, v2_ontology):
        plan = v1_ontology.migration_plan(v2_ontology)
        removals = plan["removals"]
        removed_types = [(r["type"], r.get("label") or r.get("relationship")) for r in removals]
        assert ("node", "OldEntity") in removed_types
        assert ("relationship", "OLD_REL") in removed_types
        assert ("property", "age") in [(r["type"], r.get("property")) for r in removals]

    def test_generates_cypher_for_removals(self, v1_ontology, v2_ontology):
        plan = v1_ontology.migration_plan(v2_ontology)
        cyphers = [s["cypher"] for s in plan["cypher_statements"]]
        assert any("OldEntity" in c and "DELETE" in c for c in cyphers)
        assert any("OLD_REL" in c and "DELETE" in c for c in cyphers)
        assert any("age" in c and "REMOVE" in c for c in cyphers)

    def test_marks_breaking(self, v1_ontology, v2_ontology):
        plan = v1_ontology.migration_plan(v2_ontology)
        assert plan["breaking"] is True

    def test_no_op_migration(self, v1_ontology):
        plan = v1_ontology.migration_plan(v1_ontology)
        assert plan["cypher_statements"] == []
        assert plan["additions"] == []
        assert plan["removals"] == []
        assert plan["breaking"] is False

    def test_summary_present(self, v1_ontology, v2_ontology):
        plan = v1_ontology.migration_plan(v2_ontology)
        assert "summary" in plan
        assert "1.0" in plan["summary"]
        assert "2.0" in plan["summary"]


# ---------------------------------------------------------------------------
# Tests: apply_migration
# ---------------------------------------------------------------------------

class TestApplyMigration:
    def test_dry_run_does_not_execute(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()
        result = v1_ontology.apply_migration(store, v2_ontology, dry_run=True)
        assert result["dry_run"] is True
        assert result["executed"] == []
        assert len(store.writes) == 0
        # Plan should still be present
        assert len(result["plan"]["cypher_statements"]) > 0

    def test_executes_all_statements(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()
        result = v1_ontology.apply_migration(store, v2_ontology, database="testdb")
        expected_count = len(result["plan"]["cypher_statements"])
        assert len(result["executed"]) == expected_count
        assert len(result["errors"]) == 0
        # All writes targeted the right database
        for w in store.writes:
            assert w["database"] == "testdb"

    def test_captures_errors(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()

        def failing_write(cypher, *, params=None, database="neo4j"):
            if "OldEntity" in cypher:
                raise RuntimeError("simulated DB error")
            return {"nodes_affected": 0, "relationships_affected": 0, "properties_set": 0}

        store.execute_write = failing_write
        result = v1_ontology.apply_migration(store, v2_ontology)
        assert len(result["errors"]) > 0
        assert "simulated DB error" in result["errors"][0]["error"]
        # Remaining statements should still have been attempted
        total = len(result["executed"]) + len(result["errors"])
        assert total == len(result["plan"]["cypher_statements"])

    def test_no_op_returns_empty_executed(self, v1_ontology):
        store = FakeGraphStore()
        result = v1_ontology.apply_migration(store, v1_ontology)
        assert result["executed"] == []
        assert result["errors"] == []
        assert len(store.writes) == 0


# ---------------------------------------------------------------------------
# Tests: Seocho.migrate()
# ---------------------------------------------------------------------------

class TestSeochoMigrate:
    def _make_client(self, ontology, graph_store):
        """Create a minimal local-mode Seocho with a fake engine."""
        from seocho.client import Seocho

        client = Seocho.__new__(Seocho)
        client.ontology = ontology
        client._local_mode = True
        client._ontology_registry = {}
        client._graph_catalog_cache = None

        engine = MagicMock()
        engine.graph_store = graph_store
        client._engine = engine
        return client

    def test_migrate_dry_run(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()
        client = self._make_client(v1_ontology, store)
        client.register_ontology("mydb", v1_ontology)

        result = client.migrate("mydb", v2_ontology, dry_run=True)
        assert result["dry_run"] is True
        assert len(store.writes) == 0
        # Ontology should NOT be updated
        assert client.get_ontology("mydb") is v1_ontology

    def test_migrate_executes_and_updates_registry(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()
        client = self._make_client(v1_ontology, store)
        client.register_ontology("mydb", v1_ontology)

        result = client.migrate("mydb", v2_ontology)
        assert result["ontology_updated"] is True
        assert client.get_ontology("mydb") is v2_ontology
        assert len(store.writes) > 0
        # Constraints should have been applied
        assert len(store.constraints_applied) == 1
        assert store.constraints_applied[0]["database"] == "mydb"

    def test_migrate_skips_registry_on_error(self, v1_ontology, v2_ontology):
        store = FakeGraphStore()

        def always_fail(cypher, *, params=None, database="neo4j"):
            raise RuntimeError("fail")

        store.execute_write = always_fail
        client = self._make_client(v1_ontology, store)
        client.register_ontology("mydb", v1_ontology)

        result = client.migrate("mydb", v2_ontology)
        assert result["ontology_updated"] is False
        # Original ontology preserved
        assert client.get_ontology("mydb") is v1_ontology

    def test_migrate_requires_local_mode(self, v1_ontology, v2_ontology):
        from seocho.client import Seocho

        client = Seocho.__new__(Seocho)
        client._local_mode = False
        client._engine = None
        client._ontology_registry = {}
        client.ontology = v1_ontology

        with pytest.raises(RuntimeError, match="local mode"):
            client.migrate("mydb", v2_ontology)
