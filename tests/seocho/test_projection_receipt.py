from __future__ import annotations

import hashlib
import json

import pytest

from seocho.ontology.projection_receipt import ProjectionReceiptError, validate_projection_receipt


def test_projection_receipt_binds_governance_and_agent_profile() -> None:
    digest = "a" * 64
    profile = {"schema_version": "seocho.agent_ontology_profile.v1", "canonical_bundle_sha256": digest, "purpose": "projection"}
    profile["profile_sha256"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = validate_projection_receipt(
        {"schema_version": "seocho.rdf_governance_receipt.v1", "bundle_sha256": digest, "data_graph_sha256": "c" * 64, "promotable": True},
        profile,
    )
    assert receipt["rdf_bundle_sha256"] == digest
    assert len(receipt["projection_receipt_sha256"]) == 64


def test_projection_receipt_rejects_unpromotable_or_mismatched_profile() -> None:
    with pytest.raises(ProjectionReceiptError):
        validate_projection_receipt(
            {"schema_version": "seocho.rdf_governance_receipt.v1", "bundle_sha256": "a" * 64, "data_graph_sha256": "b" * 64, "promotable": False},
            {"schema_version": "seocho.agent_ontology_profile.v1", "canonical_bundle_sha256": "a" * 64, "profile_sha256": "c" * 64},
        )


def test_projection_receipt_rejects_tampered_or_wrong_purpose_profile() -> None:
    profile = {"schema_version": "seocho.agent_ontology_profile.v1", "canonical_bundle_sha256": "a" * 64, "purpose": "query"}
    profile["profile_sha256"] = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ProjectionReceiptError, match="projection agent profile"):
        validate_projection_receipt(
            {"schema_version": "seocho.rdf_governance_receipt.v1", "bundle_sha256": "a" * 64, "data_graph_sha256": "b" * 64, "promotable": True}, profile,
        )
