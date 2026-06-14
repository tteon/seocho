"""Tests for the ambiguity review loop — Phase 1 (quarantine, detect, mapping-spec)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import (
    AmbiguityQuarantine,
    AmbiguousEntity,
    apply_mapping_spec,
    detect_ambiguities,
    starter_mapping_spec,
)


def _onto() -> Ontology:
    return Ontology("biz", version="1.0.0", nodes={
        "Company": NodeDef(description="A company.", aliases=["Firm"], properties={"name": P(str, unique=True)}),
        "Person": NodeDef(description="A person.", aliases=["Firm"], properties={"name": P(str, unique=True)}),
        "Concept": NodeDef(description="A concept."),
    })


def _graph() -> dict:
    return {"nodes": [
        {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},          # clean
        {"id": "r1", "label": "Regulation", "properties": {"name": "Basel III"}},  # OOV
        {"id": "e1", "label": "Entity", "properties": {"name": "mystery thing"}},  # fallback
        {"id": "o1", "label": "Risk", "properties": {"name": "FX risk", "_out_of_ontology": "true"}},  # stamped
        {"id": "f1", "label": "Company", "properties": {"name": "Firm"}},          # alias collision (Company+Person)
    ], "relationships": []}


def test_detect_signals():
    found = detect_ambiguities(_graph(), _onto(), source="doc1", workspace_id="ws1")
    by_signal = {f.signal for f in found}
    assert "oov" in by_signal            # Regulation
    assert "entity_fallback" in by_signal  # Entity
    assert "out_of_ontology" in by_signal  # stamped Risk
    assert "alias_collision" in by_signal  # Firm → Company+Person
    # clean Company/Acme not flagged
    assert all(f.surface != "Acme" for f in found)
    assert all(f.workspace_id == "ws1" for f in found)


def test_quarantine_roundtrip_and_clusters(tmp_path):
    q = AmbiguityQuarantine(tmp_path / "q.jsonl")
    found = detect_ambiguities(_graph(), _onto())
    # add twice to build frequency
    q.add(found); q.add(found)
    clusters = q.clusters()
    assert clusters[0]["frequency"] >= clusters[-1]["frequency"]  # sorted desc
    reg = next(c for c in clusters if c["surface"] == "Basel III")
    assert reg["frequency"] == 2
    assert reg["signals"].get("oov") == 2


def test_starter_spec_suggests_actions():
    q_clusters = [
        {"surface": "Basel III", "frequency": 5, "signals": {"oov": 5}, "labels": ["Regulation"], "candidate_labels": [], "examples": []},
        {"surface": "Firm", "frequency": 3, "signals": {"alias_collision": 3}, "labels": ["Company"], "candidate_labels": ["Company", "Person"], "examples": []},
        {"surface": "lower-case noise", "frequency": 1, "signals": {"oov": 1}, "labels": ["x"], "candidate_labels": [], "examples": []},
    ]
    spec = starter_mapping_spec(q_clusters, _onto())
    actions = {m["surface"]: m["action"] for m in spec["mappings"]}
    assert actions["Firm"] == "alias"            # has candidate labels
    assert actions["Basel III"] == "new_class"   # capitalized, no candidate
    assert actions["lower-case noise"] == "ignore"


def test_apply_alias_and_new_class():
    onto = _onto()
    spec = {"mappings": [
        {"surface": "Adj. EBITDA", "action": "alias", "target": "Company"},
        {"surface": "Basel III", "action": "new_class", "target": "Regulation", "parent": "Concept",
         "description": "A regulatory rule."},
        {"surface": "noise", "action": "ignore"},
    ]}
    new_onto = apply_mapping_spec(onto, spec)
    assert "Regulation" in new_onto.nodes
    assert new_onto.nodes["Regulation"].broader == ["Concept"]
    assert "Adj. EBITDA" in new_onto.nodes["Company"].aliases
    assert new_onto.version == "1.1.0"   # minor bump
    # original ontology unchanged
    assert "Regulation" not in onto.nodes


def test_apply_rejects_bad_parent_and_action():
    import pytest
    onto = _onto()
    with pytest.raises(ValueError):
        apply_mapping_spec(onto, {"mappings": [{"surface": "X", "action": "new_class", "target": "X", "parent": "Nonexistent"}]})
    with pytest.raises(ValueError):
        apply_mapping_spec(onto, {"mappings": [{"surface": "X", "action": "frobnicate"}]})


def test_apply_then_score_improves_corpus_coverage():
    # the loop's payoff: registering a needed class lifts corpus_coverage
    from seocho.ontology_scorecard import build_corpus_profile, score_ontology
    corpus = build_corpus_profile([{"nodes": [{"label": "Regulation"}, {"label": "Company"}]}])
    onto = _onto()
    before = score_ontology(onto, corpus_profile=corpus).dimension("corpus_coverage").score
    new_onto = apply_mapping_spec(onto, {"mappings": [
        {"surface": "Basel III", "action": "new_class", "target": "Regulation", "parent": "Concept"}]})
    after = score_ontology(new_onto, corpus_profile=corpus).dimension("corpus_coverage").score
    assert after > before
