"""Tests for Ontology.from_artifact() and Ontology.coverage_stats()."""

from typing import Any, Dict, List, Optional

import pytest

from seocho.ontology import NodeDef, Ontology, P, PropertyType, RelDef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artifact_dict(
    *,
    ontology_name: str = "test",
    classes: Optional[List[Dict]] = None,
    relationships: Optional[List[Dict]] = None,
    shapes: Optional[List[Dict]] = None,
    source_summary: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Build a minimal artifact dict for testing."""
    return {
        "ontology_candidate": {
            "ontology_name": ontology_name,
            "classes": classes or [],
            "relationships": relationships or [],
        },
        "shacl_candidate": {
            "shapes": shapes or [],
        },
        "source_summary": source_summary or {},
    }


class FakeGraphStore:
    """Graph store that returns configurable counts per label/type."""

    def __init__(self, node_counts: Optional[Dict[str, int]] = None, rel_counts: Optional[Dict[str, int]] = None):
        self._node_counts = node_counts or {}
        self._rel_counts = rel_counts or {}

    def query(self, cypher: str, *, params=None, database="neo4j") -> List[Dict[str, Any]]:
        # Parse label/type from the Cypher MATCH pattern
        for label, count in self._node_counts.items():
            if f"`{label}`" in cypher and "count(n)" in cypher:
                return [{"cnt": count}]
        for rtype, count in self._rel_counts.items():
            if f"`{rtype}`" in cypher and "count(r)" in cypher:
                return [{"cnt": count}]
        return [{"cnt": 0}]


# ---------------------------------------------------------------------------
# Tests: Ontology.from_artifact()
# ---------------------------------------------------------------------------

class TestFromArtifact:
    def test_basic_round_trip(self):
        """Ontology → artifact → Ontology preserves structure."""
        original = Ontology(
            name="finance",
            version="1.0",
            nodes={
                "Company": NodeDef(
                    description="A company",
                    properties={"name": P(str, unique=True), "ticker": P(str)},
                    aliases=["Corp", "Firm"],
                ),
                "Person": NodeDef(
                    description="A person",
                    properties={"name": P(str), "age": P(int)},
                ),
            },
            relationships={
                "WORKS_AT": RelDef(source="Person", target="Company", description="Employment"),
            },
        )
        draft = original.to_semantic_artifact_draft()
        restored = Ontology.from_artifact(draft, version="1.0")

        assert restored.name == "finance"
        assert set(restored.nodes.keys()) == {"Company", "Person"}
        assert set(restored.relationships.keys()) == {"WORKS_AT"}
        assert restored.nodes["Company"].description == "A company"
        assert "name" in restored.nodes["Company"].properties
        assert restored.nodes["Person"].properties["age"].property_type == PropertyType.INTEGER
        assert restored.relationships["WORKS_AT"].source == "Person"
        assert restored.relationships["WORKS_AT"].target == "Company"

    def test_from_dict(self):
        """Can build from a plain dict."""
        data = _make_artifact_dict(
            ontology_name="myonto",
            classes=[
                {"name": "Entity", "description": "Generic", "properties": [
                    {"name": "id", "datatype": "string"},
                    {"name": "score", "datatype": "float"},
                ]},
            ],
            relationships=[
                {"type": "LINKS_TO", "source": "Entity", "target": "Entity"},
            ],
        )
        onto = Ontology.from_artifact(data, version="2.0")
        assert onto.name == "myonto"
        assert onto.version == "2.0"
        assert "Entity" in onto.nodes
        assert onto.nodes["Entity"].properties["score"].property_type == PropertyType.FLOAT
        assert "LINKS_TO" in onto.relationships

    def test_shacl_enrichment_marks_required(self):
        """SHACL minCount=1 constraints are carried as required=True."""
        data = _make_artifact_dict(
            classes=[{"name": "Person", "properties": [{"name": "name", "datatype": "string"}]}],
            shapes=[{
                "target_class": "Person",
                "constraints": [
                    {"path": "name", "constraint": "minCount", "params": {"value": 1}},
                ],
            }],
        )
        onto = Ontology.from_artifact(data)
        assert onto.nodes["Person"].properties["name"].required is True

    def test_empty_artifact(self):
        """Empty artifact produces an ontology with no nodes/rels."""
        data = _make_artifact_dict(ontology_name="")
        onto = Ontology.from_artifact(data)
        assert onto.name == "Unnamed"
        assert len(onto.nodes) == 0
        assert len(onto.relationships) == 0

    def test_preserves_aliases(self):
        """Aliases from the artifact are preserved."""
        data = _make_artifact_dict(
            classes=[{
                "name": "Company",
                "aliases": ["Corp", "Firm"],
                "properties": [],
            }],
        )
        onto = Ontology.from_artifact(data)
        assert onto.nodes["Company"].aliases == ["Corp", "Firm"]

    def test_source_summary_metadata(self):
        """Source summary metadata is used for package_id and namespace."""
        data = _make_artifact_dict(
            source_summary={
                "package_id": "fin-onto",
                "namespace": "https://example.org/finance",
                "version": "3.0",
            },
        )
        onto = Ontology.from_artifact(data, version="3.0")
        assert onto.package_id == "fin-onto"
        assert onto.namespace == "https://example.org/finance"


# ---------------------------------------------------------------------------
# Tests: Ontology.coverage_stats()
# ---------------------------------------------------------------------------

class TestCoverageStats:
    def test_full_coverage(self):
        """All defined types have data → score 1.0."""
        onto = Ontology(
            name="test",
            nodes={"Person": NodeDef(properties={"name": P(str)})},
            relationships={"KNOWS": RelDef(source="Person", target="Person")},
        )
        store = FakeGraphStore(
            node_counts={"Person": 10},
            rel_counts={"KNOWS": 5},
        )
        stats = onto.coverage_stats(store)
        assert stats["overall_score"] == 1.0
        assert stats["node_coverage"]["populated"] == 1
        assert stats["relationship_coverage"]["populated"] == 1
        assert stats["unused"]["node_types"] == []
        assert stats["unused"]["relationship_types"] == []

    def test_partial_coverage(self):
        """Some types have no data → score < 1.0."""
        onto = Ontology(
            name="test",
            nodes={
                "Person": NodeDef(properties={"name": P(str)}),
                "Company": NodeDef(properties={"name": P(str)}),
            },
            relationships={
                "KNOWS": RelDef(source="Person", target="Person"),
                "WORKS_AT": RelDef(source="Person", target="Company"),
            },
        )
        store = FakeGraphStore(
            node_counts={"Person": 10},  # Company missing
            rel_counts={"KNOWS": 5},     # WORKS_AT missing
        )
        stats = onto.coverage_stats(store)
        assert stats["overall_score"] == 0.5
        assert stats["unused"]["node_types"] == ["Company"]
        assert stats["unused"]["relationship_types"] == ["WORKS_AT"]

    def test_empty_graph(self):
        """Empty graph → score 0.0, all types unused."""
        onto = Ontology(
            name="test",
            nodes={"A": NodeDef(properties={}), "B": NodeDef(properties={})},
            relationships={"R": RelDef(source="A", target="B")},
        )
        store = FakeGraphStore()
        stats = onto.coverage_stats(store)
        assert stats["overall_score"] == 0.0
        assert len(stats["unused"]["node_types"]) == 2
        assert len(stats["unused"]["relationship_types"]) == 1

    def test_empty_ontology(self):
        """Ontology with no definitions → score 1.0 (vacuous truth)."""
        onto = Ontology(name="empty", nodes={}, relationships={})
        store = FakeGraphStore()
        stats = onto.coverage_stats(store)
        assert stats["overall_score"] == 1.0

    def test_details_contain_counts(self):
        """Details include per-label counts."""
        onto = Ontology(
            name="test",
            nodes={"Person": NodeDef(properties={"name": P(str)})},
            relationships={},
        )
        store = FakeGraphStore(node_counts={"Person": 42})
        stats = onto.coverage_stats(store)
        assert stats["node_coverage"]["details"][0]["label"] == "Person"
        assert stats["node_coverage"]["details"][0]["count"] == 42
