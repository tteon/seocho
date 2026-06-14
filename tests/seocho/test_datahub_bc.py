"""Tests for DataHub connector Phase B/C (ADR-0129)."""

from __future__ import annotations

import json

from seocho.datahub_export import (
    ambiguity_clusters_to_glossary_proposals,
    numeric_validation_to_assertions,
    scorecard_to_structured_properties,
)

_CLUSTERS = [
    {"surface": "Regulation", "frequency": 12, "signals": {"oov": 12}, "candidate_labels": [], "examples": ["Basel III"]},
    {"surface": "Adj. EBITDA", "frequency": 7, "signals": {"oov": 7}, "candidate_labels": ["FinancialMetric"], "examples": []},
]


def test_clusters_to_proposed_terms():
    mcps = ambiguity_clusters_to_glossary_proposals(_CLUSTERS, package_id="fin")
    terms = [m for m in mcps if m["entityType"] == "glossaryTerm"]
    nodes = [m for m in mcps if m["entityType"] == "glossaryNode"]
    assert len(terms) == 2 and len(nodes) == 1            # 2 proposed terms + 1 Proposed node
    reg = next(m for m in terms if m["aspect"]["name"] == "Regulation")
    assert reg["entityUrn"] == "urn:li:glossaryTerm:fin.proposed.Regulation"   # deterministic
    cp = reg["aspect"]["customProperties"]
    assert cp["review_status"] == "PROPOSED" and cp["frequency"] == "12"
    assert nodes[0]["aspect"]["parentNode"] == "urn:li:glossaryNode:fin"


def test_scorecard_to_structured_properties():
    sc = {"overall_score": 0.92, "grade": "A", "blocking": False,
          "dimensions": [{"name": "taxonomy_health", "score": 0.8}, {"name": "corpus_coverage", "score": 0.6}]}
    mcps = scorecard_to_structured_properties(sc, target_urn="urn:li:glossaryNode:fin")
    assert len(mcps) == 1 and mcps[0]["aspectName"] == "structuredProperties"
    keys = {p["propertyUrn"] for p in mcps[0]["aspect"]["properties"]}
    assert "urn:li:structuredProperty:seocho.scorecard.overall_score" in keys
    assert "urn:li:structuredProperty:seocho.scorecard.taxonomy_health" in keys
    assert "urn:li:structuredProperty:seocho.scorecard.corpus_coverage" in keys


def test_numeric_validation_to_assertions_pass_and_fail():
    clean = {"findings": [], "confidence": 1.0}
    dirty = {"findings": [{"severity": "warn", "code": "reconciliation", "message": "sum != total"}], "confidence": 0.66}
    ds = "urn:li:dataset:(urn:li:dataPlatform:seocho,fin_graph,PROD)"
    ok = numeric_validation_to_assertions(clean, dataset_urn=ds)
    bad = numeric_validation_to_assertions(dirty, dataset_urn=ds)
    ok_run = next(m for m in ok if m["aspectName"] == "assertionRunEvent")
    bad_run = next(m for m in bad if m["aspectName"] == "assertionRunEvent")
    assert ok_run["aspect"]["result"]["type"] == "SUCCESS"
    assert bad_run["aspect"]["result"]["type"] == "FAILURE"
    info = next(m for m in ok if m["aspectName"] == "assertionInfo")
    assert info["aspect"]["datasetAssertion"]["dataset"] == ds   # dataset urn embedded
    # both emit assertionInfo + assertionRunEvent
    assert {m["aspectName"] for m in ok} == {"assertionInfo", "assertionRunEvent"}
