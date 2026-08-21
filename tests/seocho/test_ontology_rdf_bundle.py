from __future__ import annotations

import json

import pytest

from seocho.ontology import NodeDef, Ontology, Property, build_rdf_ontology_bundle


def test_rdf_bundle_has_one_jsonld_source_and_derived_rdf_artifacts(tmp_path):
    pytest.importorskip("rdflib")
    ontology = Ontology(
        name="people", namespace="https://example.test/people#", version="1.2.3",
        nodes={"Person": NodeDef(properties={"name": Property(str, required=True)})},
    )
    bundle = build_rdf_ontology_bundle(ontology, tmp_path / "bundle")
    manifest = json.loads(bundle.manifest_path.read_text())

    assert bundle.jsonld_path.exists()
    assert "owl:Class" in bundle.turtle_path.read_text()
    assert "sh:NodeShape" in bundle.shacl_path.read_text()
    assert manifest["bundle_sha256"] == bundle.digest
    assert set(manifest["files"]) == {"ontology.jsonld", "ontology.ttl", "shapes.ttl"}
    indexing_profile = json.loads((bundle.agent_profiles_dir / "indexing.json").read_text())
    assert indexing_profile["canonical_bundle_sha256"] == bundle.digest
    assert indexing_profile["purpose"] == "indexing"
