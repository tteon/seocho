"""Tests for the versioned ontology snapshot store (Layer 3)."""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_scorecard import build_corpus_profile, score_ontology
from seocho.ontology_snapshot_store import (
    OntologySnapshotStore,
    SnapshotConflict,
)


def _v1() -> Ontology:
    return Ontology("acme", package_id="acme", version="1.0.0", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
    })


def _v2() -> Ontology:
    return Ontology("acme", package_id="acme", version="2.0.0", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
        "Person": NodeDef(description="A person.", properties={"name": P(str, unique=True)}),
        "Regulation": NodeDef(description="A rule.", properties={"name": P(str, unique=True)}),
    })


_CORPUS = build_corpus_profile([
    {"nodes": [{"label": "Company"}, {"label": "Person"}, {"label": "Regulation"}]},
    {"nodes": [{"label": "Person"}, {"label": "FinancialMetric"}]},
], source="test")


def test_save_get_roundtrip(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    snap = store.save(_v1(), notes="initial")
    assert snap.schema_fingerprint
    got = store.get("acme", "1.0.0")
    assert got is not None
    assert got.notes == "initial"
    o = got.load_ontology()
    assert set(o.nodes) == {"Company", "FinancialMetric"}


def test_idempotent_same_content(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v1())
    store.save(_v1())  # same version + same fingerprint → no error
    assert len([s for s in store.list("acme") if s.version == "1.0.0"]) == 1


def test_immutability_conflict_on_changed_content(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v1())
    mutated = _v1()
    mutated.nodes["Extra"] = NodeDef(description="sneaky change", properties={"id": P(str, unique=True)})
    with pytest.raises(SnapshotConflict):
        store.save(mutated)  # same version 1.0.0, different schema


def test_list_and_latest_ordering(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v2())
    store.save(_v1())
    versions = [s.version for s in store.list("acme")]
    assert versions == ["1.0.0", "2.0.0"]  # semver order regardless of save order
    assert store.latest("acme").version == "2.0.0"


def test_history_carries_evidence(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v1(), scorecard=score_ontology(_v1(), corpus_profile=_CORPUS, profile="guardrail"),
               corpus_profile=_CORPUS, weight_profile="guardrail")
    hist = store.history("acme")
    assert hist[0]["version"] == "1.0.0"
    assert hist[0]["grade"] is not None
    assert hist[0]["corpus_coverage"] is not None


def test_compare_reports_diff_and_guardrail_verdict(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v1(), scorecard=score_ontology(_v1(), corpus_profile=_CORPUS, profile="guardrail"),
               corpus_profile=_CORPUS, weight_profile="guardrail")
    store.save(_v2(), scorecard=score_ontology(_v2(), corpus_profile=_CORPUS, profile="guardrail"),
               corpus_profile=_CORPUS, weight_profile="guardrail")
    cmp = store.compare("acme", "1.0.0", "2.0.0")
    assert cmp["schema_changed"] is True
    assert cmp["recommended_bump"] in {"major", "minor", "patch"}
    # v2 adds Person+Regulation which the corpus needs → better guardrail
    assert cmp["guardrail_verdict"]["basis"] == "corpus_coverage"
    assert cmp["guardrail_verdict"]["verdict"] == "better"
    assert cmp["guardrail_verdict"]["delta"] > 0


def test_compare_missing_version_raises(tmp_path):
    store = OntologySnapshotStore(tmp_path)
    store.save(_v1())
    with pytest.raises(KeyError):
        store.compare("acme", "1.0.0", "9.9.9")
