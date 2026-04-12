from __future__ import annotations

import sys
import types

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_governance import (
    check_ontology,
    diff_ontologies,
    export_ontology_payload,
    inspect_owl_ontology,
)


def _ontology(*, version: str = "1.0.0") -> Ontology:
    return Ontology(
        name="finance",
        version=version,
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str, index=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE"),
        },
    )


def test_check_ontology_warns_when_unique_key_missing() -> None:
    ontology = Ontology(
        name="warning_case",
        nodes={"LooseEntity": NodeDef(properties={"name": P(str)})},
        relationships={},
    )

    result = check_ontology(ontology)

    assert result.ok is True
    assert result.errors == []
    assert any("consider adding one" in item for item in result.warnings)


def test_diff_ontologies_detects_metadata_and_schema_changes() -> None:
    left = _ontology(version="1.0.0")
    right = Ontology(
        name="finance",
        version="1.1.0",
        graph_model="rdf",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str, index=True)}),
            "Desk": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "ASSIGNED_TO": RelDef(source="Person", target="Desk", cardinality="MANY_TO_ONE"),
        },
    )

    diff = diff_ontologies(left, right)

    assert "version" in diff.changes["metadata"]["changed"]
    assert "graph_model" in diff.changes["metadata"]["changed"]
    assert "Desk" in diff.changes["nodes"]["added"]
    assert "Person" in diff.changes["nodes"]["removed"]
    assert "ASSIGNED_TO" in diff.changes["relationships"]["added"]
    assert "WORKS_AT" in diff.changes["relationships"]["removed"]


def test_export_ontology_payload_shacl_contains_shapes() -> None:
    payload = export_ontology_payload(_ontology(), output_format="shacl")
    assert isinstance(payload, dict)
    assert "shapes" in payload
    assert len(payload["shapes"]) == 2


def test_inspect_owl_ontology_uses_optional_owlready2(monkeypatch) -> None:
    class FakeLoadedOntology:
        def classes(self):
            return [types.SimpleNamespace(name="Company"), types.SimpleNamespace(name="Person")]

        def individuals(self):
            return [types.SimpleNamespace(name="Acme")]

        def properties(self):
            return [types.SimpleNamespace(name="worksAt")]

        @property
        def imported_ontologies(self):
            return [types.SimpleNamespace(name="base")]

    class FakeOntologyRef:
        def load(self):
            return FakeLoadedOntology()

    fake_module = types.SimpleNamespace(get_ontology=lambda source: FakeOntologyRef())
    monkeypatch.setitem(sys.modules, "owlready2", fake_module)

    result = inspect_owl_ontology("file:///tmp/fake.owl")

    assert result.available is True
    assert result.error is None
    assert result.stats["class_count"] == 2
    assert result.stats["property_count"] == 1
