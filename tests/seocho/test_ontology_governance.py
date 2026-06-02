from __future__ import annotations

import sys
import types

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_governance import (
    build_ontology_governance_report,
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
    assert result.package_id == "warning_case"
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

    assert diff.package_id == "finance"
    assert diff.recommended_bump == "major"
    assert diff.requires_migration is True
    assert "version" in diff.changes["metadata"]["changed"]
    assert "graph_model" in diff.changes["metadata"]["changed"]
    assert "Desk" in diff.changes["nodes"]["added"]
    assert "Person" in diff.changes["nodes"]["removed"]
    assert "ASSIGNED_TO" in diff.changes["relationships"]["added"]
    assert "WORKS_AT" in diff.changes["relationships"]["removed"]
    assert any("major version bump" in item for item in diff.migration_warnings)


def test_export_ontology_payload_shacl_contains_shapes() -> None:
    payload = export_ontology_payload(_ontology(), output_format="shacl")
    assert isinstance(payload, dict)
    assert "shapes" in payload
    assert len(payload["shapes"]) == 2


def test_diff_ontologies_warns_on_package_boundary_change() -> None:
    left = Ontology(
        name="finance",
        package_id="package.finance",
        version="1.0.0",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    right = Ontology(
        name="finance_v2",
        package_id="package.finance.v2",
        version="2.0.0",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )

    diff = diff_ontologies(left, right)

    assert diff.package_id == "package.finance.v2"
    assert any("package migration boundary" in item for item in diff.migration_warnings)


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


def test_governance_report_includes_context_hash_and_shacl_stats(tmp_path) -> None:
    pytest.importorskip("rdflib", reason="TTL governance report requires rdflib (install via [ontology] extra)")
    ttl_path = tmp_path / "finance.ttl"
    ttl_path.write_text(
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <https://example.com/finance#> .

ex:FinanceOntology a owl:Ontology ;
    rdfs:label "Finance TTL" ;
    owl:versionInfo "1.2.0" .

ex:Company a owl:Class .
ex:FinancialMetric a owl:Class .

ex:reported a owl:ObjectProperty ;
    rdfs:domain ex:Company ;
    rdfs:range ex:FinancialMetric .

ex:name a owl:DatatypeProperty ;
    rdfs:domain ex:Company ;
    rdfs:range xsd:string .

ex:year a owl:DatatypeProperty ;
    rdfs:domain ex:FinancialMetric ;
    rdfs:range xsd:integer .
""".strip(),
        encoding="utf-8",
    )

    report = build_ontology_governance_report(
        ttl_path,
        include_owl_inspection=False,
    )

    assert report.ok is True
    assert report.context_descriptor["ontology_id"] == "FinanceOntology"
    assert report.context_descriptor["ontology_version"] == "1.2.0"
    assert report.context_descriptor["context_hash"]
    assert report.shacl_export["stats"]["node_shape_count"] == 2
    assert report.shacl_export["stats"]["property_shape_count"] >= 2
    assert report.sample_data_validation.ok is True
    assert report.owlready2_inspection is None
