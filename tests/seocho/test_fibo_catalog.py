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


def test_handles_list_valued_domain_range_and_subclass():
    """Real FIBO properties carry list-valued domain/range/subClassOf — must not
    crash (regression: 'unhashable type: list')."""
    BE = "https://spec.edmcouncil.org/fibo/ontology/BE/"
    catalog = {
        "schema_version": "seocho.fibo_catalog.v1", "snapshot_hash": "h", "fibo_commit": "c",
        "modules": {"BE": {"code": "BE", "iri_prefix": BE, "summary": "s",
            "label_index": {}, "definitions": {},
            "resources": {
                BE + "A": {"kind": "class", "local_name": "A", "label": "A", "subclass_of": [], "domain": "", "range": ""},
                BE + "B": {"kind": "class", "local_name": "B", "label": "B",
                           "subclass_of": [BE + "A", BE + "Missing"], "domain": "", "range": ""},
                BE + "rel": {"kind": "object_property", "local_name": "rel", "label": "rel",
                             "domain": [BE + "A", BE + "B"], "range": [BE + "B"], "subclass_of": []},
            }}}}
    onto = catalog_module_to_ontology(catalog, "BE")
    assert onto.nodes["B"].broader == ["A"]              # missing parent dropped, list handled
    assert onto.relationships["REL"].source == "A"        # first of domain list
    assert onto.relationships["REL"].target == "B"        # first of range list


def test_alias_bridge_token_subset_match_no_spurious():
    from seocho.ontology import NodeDef, Ontology
    from seocho.fibo_catalog import alias_bridge, bridge_to_corpus
    from seocho.ontology_scorecard import build_corpus_profile

    onto = Ontology("m", nodes={
        "JointStockCompany": NodeDef(description="c"),
        "PubliclyHeldCompany": NodeDef(description="c"),
        "Candidate": NodeDef(description="c"),   # must NOT get a 'Date' alias
        "FinancialInstrument": NodeDef(description="c"),
    })
    bridged = alias_bridge(onto, ["Company", "Date", "FinancialMetric"])
    assert "Company" in bridged.nodes["JointStockCompany"].aliases     # token subset {company} ⊆ {joint,stock,company}
    assert "Company" in bridged.nodes["PubliclyHeldCompany"].aliases
    assert "Date" not in bridged.nodes["Candidate"].aliases            # no spurious substring match
    # FinancialMetric ({financial,metric}) ⊄ FinancialInstrument ({financial,instrument})
    assert "FinancialMetric" not in bridged.nodes["FinancialInstrument"].aliases

    # bridging lifts corpus_coverage: a Company-heavy corpus now matches the FIBO classes
    corpus = build_corpus_profile([{"nodes": [{"label": "Company"}, {"label": "Company"}]}])
    from seocho.ontology_scorecard import score_ontology
    before = score_ontology(onto, corpus_profile=corpus, profile="guardrail").dimension("corpus_coverage").score
    after = score_ontology(bridge_to_corpus(onto, corpus), corpus_profile=corpus, profile="guardrail").dimension("corpus_coverage").score
    assert after > before


def test_semantic_bridge_propagates_down_subclassof():
    from seocho.ontology import NodeDef, Ontology
    from seocho.fibo_catalog import semantic_bridge
    from seocho.ontology_scorecard import build_corpus_profile, score_ontology

    # LegalEntity (root, label has no 'Company' token) → Corporation → Subsidiary
    onto = Ontology("m", nodes={
        "LegalEntity": NodeDef(description="root"),
        "Corporation": NodeDef(description="c", broader=["LegalEntity"]),
        "Subsidiary": NodeDef(description="s", broader=["Corporation"]),
        "Unrelated": NodeDef(description="u"),
    })
    bridged = semantic_bridge(onto, {"Company": ["LegalEntity"]})
    # generic 'Company' propagated to the root + all descendants, not the unrelated class
    assert "Company" in bridged.nodes["LegalEntity"].aliases
    assert "Company" in bridged.nodes["Corporation"].aliases
    assert "Company" in bridged.nodes["Subsidiary"].aliases
    assert "Company" not in bridged.nodes["Unrelated"].aliases
    # a 'Company' corpus is now covered though no FIBO label lexically contains 'Company'
    corpus = build_corpus_profile([{"nodes": [{"label": "Company"}]}])
    before = score_ontology(onto, corpus_profile=corpus, profile="guardrail").dimension("corpus_coverage").score
    after = score_ontology(bridged, corpus_profile=corpus, profile="guardrail").dimension("corpus_coverage").score
    assert before == 0.0 and after == 1.0


def test_semantic_bridge_ignores_absent_roots():
    from seocho.ontology import NodeDef, Ontology
    from seocho.fibo_catalog import semantic_bridge
    onto = Ontology("m", nodes={"Foo": NodeDef(description="f")})
    bridged = semantic_bridge(onto, {"Company": ["LegalEntity"]})  # root absent → no-op
    assert bridged.nodes["Foo"].aliases == []
