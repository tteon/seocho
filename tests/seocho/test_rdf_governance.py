from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, build_rdf_ontology_bundle
from seocho.ontology.governance import GovernanceValidationResult
from seocho.ontology.rdf_governance import run_rdf_governance, verify_rdf_ontology_bundle


def _bundle(tmp_path):
    pytest.importorskip("rdflib")
    return build_rdf_ontology_bundle(
        Ontology(name="work", nodes={"Person": NodeDef()}), tmp_path / "bundle"
    )


def test_bundle_verification_rejects_tampered_artifact(tmp_path):
    bundle = _bundle(tmp_path)
    bundle.turtle_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_rdf_ontology_bundle(bundle.directory)


def test_offline_governance_receipt_is_pinned_to_bundle_and_data(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    data = tmp_path / "data.ttl"
    data.write_text("@prefix ex: <https://example.test/> .\n", encoding="utf-8")

    monkeypatch.setattr(
        "seocho.ontology.rdf_governance.validate_rdf_with_pyshacl",
        lambda *_args, **_kwargs: GovernanceValidationResult("pyshacl", True, True, None, [], {}),
    )
    receipt = run_rdf_governance(bundle.directory, data)

    assert receipt.promotable is True
    assert receipt.bundle_sha256 == bundle.digest
    assert len(receipt.data_graph_sha256) == 64
    assert receipt.owl_consistency["consistent"] is None
