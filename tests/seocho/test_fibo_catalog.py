"""Tests for the compiled-FIBO-catalog → guardrail-candidate loader (ADR-0133)."""

from __future__ import annotations

import json

import pytest

from seocho.fibo_catalog import (
    catalog_module_to_ontology,
    catalog_provenance,
    fibo_guardrail_candidates,
    load_catalog,
)
from seocho.guardrail_selector import select_guardrail
from seocho.ontology_scorecard import build_corpus_profile


def _catalog() -> dict:
    """A minimal catalog in the compiler's `seocho.fibo_catalog.v1` shape."""
    BE = "https://spec.edmcouncil.org/fibo/ontology/BE/"
    return {
        "schema_version": "seocho.fibo_catalog.v1",
        "snapshot_hash": "abc123",
        "fibo_commit": "fee10a4ebe80",
        "modules": {
            "BE": {
                "code": "BE", "iri_prefix": BE, "summary": "Business Entities.",
                "label_index": {"Legal Entity": BE + "LegalEntity", "Person": BE + "Person"},
                "definitions": {BE + "LegalEntity": "A registered business.", BE + "Subsidiary": "A controlled entity."},
                "resources": {
                    BE + "LegalEntity": {"kind": "class", "local_name": "LegalEntity", "label": "Legal Entity",
                                         "subclass_of": [], "domain": "", "range": ""},
                    BE + "Subsidiary": {"kind": "class", "local_name": "Subsidiary", "label": "Subsidiary",
                                        "subclass_of": [BE + "LegalEntity"], "domain": "", "range": ""},
                    BE + "hasSubsidiary": {"kind": "object_property", "local_name": "hasSubsidiary",
                                           "label": "has subsidiary", "domain": BE + "LegalEntity",
                                           "range": BE + "Subsidiary", "subclass_of": []},
                },
            },
        },
    }


def test_load_catalog_validates():
    assert load_catalog(_catalog())["schema_version"] == "seocho.fibo_catalog.v1"
    with pytest.raises(ValueError):
        load_catalog({"not": "a catalog"})


def test_module_to_ontology_builds_nodes_rels_broader():
    onto = catalog_module_to_ontology(_catalog(), "BE")
    assert set(onto.nodes) == {"LegalEntity", "Subsidiary"}
    assert onto.nodes["LegalEntity"].aliases == ["Legal Entity"]   # human label → alias
    assert onto.nodes["LegalEntity"].same_as.endswith("LegalEntity")
    assert onto.nodes["Subsidiary"].broader == ["LegalEntity"]      # subClassOf mapped
    assert "HASSUBSIDIARY" in onto.relationships
    rel = onto.relationships["HASSUBSIDIARY"]
    assert rel.source == "LegalEntity" and rel.target == "Subsidiary"
    assert onto.package_id == "fibo.BE"
    assert onto.version == "fee10a4ebe80"   # version-pinned to the FIBO commit


def test_provenance():
    p = catalog_provenance(_catalog())
    assert p["fibo_commit"] == "fee10a4ebe80" and p["snapshot_hash"] == "abc123"


def test_candidates_feed_guardrail_selector():
    cands = fibo_guardrail_candidates(_catalog())
    assert set(cands) == {"BE"}
    # the BE module covers a legal-entity/subsidiary corpus → usable as a guardrail
    corpus = build_corpus_profile([{"nodes": [{"label": "LegalEntity"}, {"label": "Subsidiary"}]}])
    rec = select_guardrail(cands, corpus)
    assert rec.chosen == "BE"
    assert rec.candidate_scores["BE"]["corpus_coverage"] > 0
