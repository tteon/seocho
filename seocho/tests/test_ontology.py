"""Tests for seocho.ontology — Ontology, NodeDef, RelDef, P."""

import json
import tempfile
from pathlib import Path

import pytest

from seocho.ontology import (
    Cardinality,
    NodeDef,
    Ontology,
    P,
    PropertyType,
    RelDef,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_ontology():
    return Ontology(
        name="test",
        description="Test ontology",
        nodes={
            "Person": NodeDef(
                description="A person",
                properties={"name": P(str, unique=True), "age": P(int)},
                aliases=["Individual"],
                same_as="schema:Person",
            ),
            "Company": NodeDef(
                description="A company",
                properties={"name": P(str, unique=True), "ticker": P(str, index=True)},
                same_as="schema:Organization",
            ),
        },
        relationships={
            "WORKS_AT": RelDef(
                source="Person", target="Company",
                cardinality="MANY_TO_ONE", description="Employment",
                same_as="schema:worksFor",
            ),
        },
    )


# ---------------------------------------------------------------------------
# Builder API
# ---------------------------------------------------------------------------

class TestBuilderAPI:
    def test_p_str(self):
        p = P(str, unique=True)
        assert p.property_type == PropertyType.STRING
        assert p.unique is True
        assert p.constraint.value == "UNIQUE"

    def test_p_int(self):
        p = P(int)
        assert p.property_type == PropertyType.INTEGER
        assert p.constraint is None

    def test_p_bool(self):
        p = P(bool)
        assert p.property_type == PropertyType.BOOLEAN

    def test_nodedef_introspection(self):
        nd = NodeDef(properties={
            "name": P(str, unique=True),
            "age": P(int),
            "email": P(str, index=True),
            "bio": P(str, required=True),
        })
        assert nd.unique_properties == ["name"]
        assert nd.indexed_properties == ["email"]
        assert set(nd.required_properties) == {"name", "bio"}

    def test_ontology_repr(self, simple_ontology):
        r = repr(simple_ontology)
        assert "test" in r
        assert "nodes=2" in r
        assert "relationships=1" in r


# ---------------------------------------------------------------------------
# YAML roundtrip
# ---------------------------------------------------------------------------

class TestYAML:
    def test_roundtrip(self, simple_ontology):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        simple_ontology.to_yaml(path)
        loaded = Ontology.from_yaml(path)
        assert loaded.name == simple_ontology.name
        assert set(loaded.nodes.keys()) == set(simple_ontology.nodes.keys())
        assert set(loaded.relationships.keys()) == set(simple_ontology.relationships.keys())
        Path(path).unlink()


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

class TestJSONLD:
    def test_roundtrip(self, simple_ontology):
        with tempfile.NamedTemporaryFile(suffix=".jsonld", delete=False) as f:
            path = f.name
        doc = simple_ontology.to_jsonld(path)
        assert doc["@context"]["schema"] == "https://schema.org/"
        assert doc["@type"] == "seocho:Ontology"
        assert doc["name"] == "test"
        assert "Person" in doc["nodes"]
        assert doc["nodes"]["Person"]["sameAs"] == "schema:Person"

        loaded = Ontology.from_jsonld(path)
        assert loaded == simple_ontology
        Path(path).unlink()

    def test_context_keys(self, simple_ontology):
        doc = simple_ontology.to_jsonld()
        ctx = doc["@context"]
        assert "schema" in ctx
        assert "skos" in ctx
        assert "sh" in ctx
        assert "xsd" in ctx
        assert "seocho" in ctx

    def test_same_as_preserved(self, simple_ontology):
        doc = simple_ontology.to_jsonld()
        assert doc["nodes"]["Person"]["sameAs"] == "schema:Person"
        assert doc["relationships"]["WORKS_AT"]["sameAs"] == "schema:worksFor"

    def test_from_dict(self):
        onto = Ontology.from_jsonld_dict({
            "name": "from_dict",
            "nodes": {"X": {"properties": {"id": {"type": "string", "unique": True}}}},
            "relationships": {},
        })
        assert onto.name == "from_dict"
        assert onto.nodes["X"].properties["id"].unique is True


# ---------------------------------------------------------------------------
# SHACL
# ---------------------------------------------------------------------------

class TestSHACL:
    def test_shapes_count(self, simple_ontology):
        shacl = simple_ontology.to_shacl()
        assert len(shacl["shapes"]) == 2  # Person, Company

    def test_unique_property_shape(self, simple_ontology):
        shacl = simple_ontology.to_shacl()
        person_shape = next(s for s in shacl["shapes"] if s["targetClass"] == "seocho:Person")
        name_prop = next(p for p in person_shape["properties"] if p["path"] == "seocho:name")
        assert name_prop["minCount"] == 1
        assert name_prop["maxCount"] == 1
        assert name_prop["unique"] is True

    def test_cardinality_constraint(self, simple_ontology):
        shacl = simple_ontology.to_shacl()
        person_shape = next(s for s in shacl["shapes"] if s["targetClass"] == "seocho:Person")
        rel_props = [p for p in person_shape["properties"] if p["path"] == "seocho:WORKS_AT"]
        assert len(rel_props) == 1
        assert rel_props[0]["maxCount"] == 1  # MANY_TO_ONE

    def test_validate_with_shacl_catches_missing_required(self, simple_ontology):
        data = {
            "nodes": [{"id": "p1", "label": "Person", "properties": {}}],
            "relationships": [],
        }
        errors = simple_ontology.validate_with_shacl(data)
        assert any("missing required" in e for e in errors)

    def test_validate_with_shacl_catches_wrong_type(self, simple_ontology):
        data = {
            "nodes": [{"id": "p1", "label": "Person", "properties": {"name": "Alice", "age": "not_int"}}],
            "relationships": [],
        }
        errors = simple_ontology.validate_with_shacl(data)
        assert any("expected integer" in e for e in errors)

    def test_validate_with_shacl_catches_cardinality(self, simple_ontology):
        data = {
            "nodes": [
                {"id": "p1", "label": "Person", "properties": {"name": "Alice"}},
                {"id": "c1", "label": "Company", "properties": {"name": "A"}},
                {"id": "c2", "label": "Company", "properties": {"name": "B"}},
            ],
            "relationships": [
                {"source": "p1", "target": "c1", "type": "WORKS_AT"},
                {"source": "p1", "target": "c2", "type": "WORKS_AT"},
            ],
        }
        errors = simple_ontology.validate_with_shacl(data)
        assert any("MANY_TO_ONE" in e for e in errors)

    def test_validate_clean_data(self, simple_ontology):
        data = {
            "nodes": [
                {"id": "p1", "label": "Person", "properties": {"name": "Alice", "age": 30}},
                {"id": "c1", "label": "Company", "properties": {"name": "Acme", "ticker": "ACM"}},
            ],
            "relationships": [
                {"source": "p1", "target": "c1", "type": "WORKS_AT"},
            ],
        }
        errors = simple_ontology.validate_with_shacl(data)
        assert errors == []


# ---------------------------------------------------------------------------
# Denormalization
# ---------------------------------------------------------------------------

class TestDenormalization:
    def test_plan_many_to_one(self, simple_ontology):
        plan = simple_ontology.denormalization_plan()
        assert "Person" in plan
        embeds = plan["Person"]["embeds"]
        works_at = next(e for e in embeds if e["via"] == "WORKS_AT")
        assert works_at["safe"] is True
        assert works_at["direction"] == "outgoing"
        assert "company_name" in works_at["fields"]

    def test_plan_many_to_many_blocked(self):
        onto = Ontology(
            name="t",
            nodes={
                "A": NodeDef(properties={"name": P(str, unique=True)}),
                "B": NodeDef(properties={"name": P(str, unique=True)}),
            },
            relationships={"R": RelDef(source="A", target="B", cardinality="MANY_TO_MANY")},
        )
        plan = onto.denormalization_plan()
        r_entry = plan["A"]["embeds"][0]
        assert r_entry["safe"] is False
        assert "MANY_TO_MANY" in r_entry.get("reason", "")

    def test_plan_self_referential_blocked(self):
        onto = Ontology(
            name="t",
            nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={"MANAGES": RelDef(source="Person", target="Person", cardinality="ONE_TO_ONE")},
        )
        plan = onto.denormalization_plan()
        assert plan["Person"]["embeds"][0]["safe"] is False
        assert "Self-referential" in plan["Person"]["embeds"][0]["reason"]

    def test_denormalize_and_normalize_roundtrip(self, simple_ontology):
        nodes = [
            {"id": "p1", "label": "Person", "properties": {"name": "Alice", "age": 30}},
            {"id": "c1", "label": "Company", "properties": {"name": "Acme", "ticker": "ACM"}},
        ]
        rels = [{"source": "p1", "target": "c1", "type": "WORKS_AT", "properties": {}}]

        denorm = simple_ontology.to_denormalized_view(nodes, rels)
        person = next(d for d in denorm if d["label"] == "Person")
        assert person["properties"]["company_name"] == "Acme"
        assert person["properties"]["company_ticker"] == "ACM"

        clean, inferred = simple_ontology.normalize_view(denorm)
        clean_person = next(n for n in clean if n["label"] == "Person")
        assert "company_name" not in clean_person["properties"]
        assert len(inferred) == 1
        assert inferred[0]["type"] == "WORKS_AT"


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------

class TestPromptContext:
    def test_extraction_context(self, simple_ontology):
        ctx = simple_ontology.to_extraction_context()
        assert ctx["ontology_name"] == "test"
        assert "Person" in ctx["entity_types"]
        assert "WORKS_AT" in ctx["relationship_types"]
        assert "UNIQUE" in ctx["constraints_summary"]

    def test_query_context(self, simple_ontology):
        ctx = simple_ontology.to_query_context()
        assert "graph_schema" in ctx
        assert "Person" in ctx["graph_schema"]
        assert "WORKS_AT" in ctx["graph_schema"]
        assert "UNIQUE" in ctx["query_hints"]
        assert "many-to-one" in ctx["query_hints"].lower()

    def test_linking_context(self, simple_ontology):
        ctx = simple_ontology.to_linking_context()
        assert ctx["ontology_name"] == "test"
        assert "Person" in ctx["entity_types"]


# ---------------------------------------------------------------------------
# Cypher constraints
# ---------------------------------------------------------------------------

class TestCypherConstraints:
    def test_unique_constraint(self, simple_ontology):
        stmts = simple_ontology.to_cypher_constraints()
        assert any("constraint_Person_name_unique" in s for s in stmts)
        assert any("REQUIRE n.name IS UNIQUE" in s for s in stmts)

    def test_index_statement(self, simple_ontology):
        stmts = simple_ontology.to_cypher_constraints()
        assert any("index_Company_ticker" in s for s in stmts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_validate_missing_source(self):
        onto = Ontology(
            name="t",
            nodes={"A": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={"R": RelDef(source="Missing", target="A")},
        )
        errors = onto.validate()
        assert any("unknown source" in e.lower() for e in errors)

    def test_validate_extraction_unknown_label(self, simple_ontology):
        data = {
            "nodes": [{"id": "x1", "label": "Unknown", "properties": {}}],
            "relationships": [],
        }
        errors = simple_ontology.validate_extraction(data)
        assert any("unknown label" in e.lower() for e in errors)

    def test_validate_extraction_unknown_rel(self, simple_ontology):
        data = {
            "nodes": [],
            "relationships": [{"source": "a", "target": "b", "type": "FAKE_REL"}],
        }
        errors = simple_ontology.validate_extraction(data)
        assert any("Unknown relationship" in e for e in errors)

    def test_label_safety(self, simple_ontology):
        assert simple_ontology.is_valid_label("Person") is True
        assert simple_ontology.is_valid_label("Hacker") is False
        assert simple_ontology.sanitize_label("Hacker") == "Entity"
