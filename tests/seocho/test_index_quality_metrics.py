"""The quality signals must actually be emitted, with bounded attributes.

Every one of these corresponds to a failure that a real 322-document run
produced and that nothing reported: eight distinct values for one `P(str)`
property, a node labelled `EntityType` (the prompt's own output example, leaked
into the data), and 13 documents that yielded no graph. All three were found by
reading JSONL afterwards.

A declared-but-never-emitted instrument is worse than no instrument, because the
registry looks complete. `test_production_metrics` guards the registry; these
guard the emitters.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from seocho.index import quality_metrics as qm


class _Recorder:
    """Stands in for ProductionMetrics and remembers every call."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, float, Dict[str, Any]]] = []

    def add(self, name, amount=1, attributes=None):
        self.calls.append(("add", name, amount, dict(attributes or {})))

    def record(self, name, value, attributes=None):
        self.calls.append(("record", name, value, dict(attributes or {})))

    def set(self, name, value, attributes=None):
        self.calls.append(("set", name, value, dict(attributes or {})))

    def names(self):
        return [c[1] for c in self.calls]

    def by(self, name):
        return [c for c in self.calls if c[1] == name]


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(qm, "_metrics", lambda: rec)
    return rec


def test_scorecard_emits_score_dimensions_and_weak_points(recorder):
    scorecard = {
        "overall_score": 0.83,
        "dimensions": [{"name": "taxonomy_health", "score": 0.8}],
        "weak_points": [{"dimension": "taxonomy_health", "severity": "major"}],
    }
    qm.record_scorecard(scorecard, ontology="ent", profile="guardrail")
    assert "seocho.ontology.scorecard.score" in recorder.names()
    assert "seocho.ontology.scorecard.dimension" in recorder.names()
    severity = recorder.by("seocho.ontology.weak_point.count")[0][3]["severity"]
    assert severity == "major", "severity must survive so an alert can skip minors"


def test_contract_gaps_name_each_missing_element(recorder):
    """ADR-0181: absence is expected on first import, so it is counted, not raised."""
    class _Ontology:
        description = ""
        annotations: Dict[str, Any] = {}
        nodes: Dict[str, Any] = {}

    qm.record_contract_gaps(_Ontology(), ontology="ent")
    elements = {c[3]["element"] for c in recorder.by("seocho.ontology.contract.missing")}
    assert elements == {"purpose", "competency_questions", "modelling_decisions",
                        "identity", "vocabularies"}


def test_a_complete_contract_reports_no_gaps(recorder):
    class _Node:
        identity_keys = ["name"]

    class _Ontology:
        description = "why this exists"
        annotations = {"competency_questions": ["q"], "modelling_decisions": ["d"],
                       "vocabularies": {"Decision.status": ["applied"]}}
        nodes = {"Person": _Node()}

    qm.record_contract_gaps(_Ontology(), ontology="ent")
    assert recorder.by("seocho.ontology.contract.missing") == []


def test_an_empty_extraction_is_counted_separately_from_a_bad_label(recorder):
    """Different failures: transport/parsing versus a schema violation."""
    qm.record_extraction(ontology="ent", source_type="slack", nodes=[],
                         relationships=[], allowed_labels={"Person"})
    assert recorder.by("seocho.index.extraction.empty.count")
    assert not recorder.by("seocho.index.off_ontology_label.count")


def test_an_off_ontology_label_is_counted_and_named(recorder):
    """The measured case: the prompt's own example stored as data."""
    qm.record_extraction(
        ontology="ent", source_type="gmail",
        nodes=[{"label": "EntityType"}, {"label": "Person"}],
        relationships=[], allowed_labels={"Person", "Org"},
    )
    hits = recorder.by("seocho.index.off_ontology_label.count")
    assert len(hits) == 1
    assert hits[0][3]["label"] == "EntityType"


def test_retries_carry_a_reason(recorder):
    qm.record_extraction(ontology="ent", source_type="jira", nodes=[{"label": "Person"}],
                         relationships=[], allowed_labels={"Person"},
                         retries=2, retry_reason="non_json_response")
    hit = recorder.by("seocho.index.extraction.retry.count")[0]
    assert hit[2] == 2 and hit[3]["reason"] == "non_json_response"


def test_off_vocabulary_counts_a_case_split_as_wrong(recorder):
    """CURRENT vs current lost half the rows to a literal filter; both are counted."""
    qm.record_off_vocabulary(
        ontology="ent",
        nodes=[
            {"label": "Decision", "properties": {"status": "CURRENT"}},
            {"label": "Decision", "properties": {"status": "pending"}},
            {"label": "Decision", "properties": {"status": "applied"}},
        ],
        vocabularies={"Decision.status": ["proposed", "applied", "superseded"]},
    )
    hits = recorder.by("seocho.index.off_vocabulary_value.count")
    assert len(hits) == 2, "CURRENT and pending are outside; applied is not"
    assert all(h[3]["property"] == "Decision.status" for h in hits)


def test_no_vocabulary_declared_means_no_emission(recorder):
    qm.record_off_vocabulary(ontology="ent", nodes=[{"label": "Decision",
                             "properties": {"status": "anything"}}], vocabularies={})
    assert recorder.calls == []


def test_every_emitted_name_is_a_declared_instrument():
    """A typo here would be a metric that silently goes nowhere."""
    from seocho.metrics import METRIC_SPECS

    rec = _Recorder()
    import seocho.index.quality_metrics as module

    original = module._metrics
    module._metrics = lambda: rec
    try:
        module.record_scorecard({"score": 1.0, "dimensions": [{"name": "d", "score": 1}],
                                 "weak_points": [{"dimension": "d", "severity": "minor"}]},
                                ontology="o")
        module.record_extraction(ontology="o", source_type="s", nodes=[{"label": "Bad"}],
                                 relationships=[], allowed_labels=set(), retries=1)
        module.record_off_vocabulary(ontology="o",
                                     nodes=[{"label": "D", "properties": {"p": "x"}}],
                                     vocabularies={"D.p": ["y"]})
    finally:
        module._metrics = original

    undeclared = sorted({name for _, name, _, _ in rec.calls} - set(METRIC_SPECS))
    assert not undeclared, undeclared


def test_the_overall_score_key_is_the_one_the_scorecard_emits(recorder):
    """`OntologyScorecard.to_dict()` writes `overall_score`, not `score`.

    Reading the wrong key made this emitter skip in silence: a grade was still
    produced, so nothing looked broken, and the single panel answering "is this
    ontology good enough" would have had no data. Both spellings are accepted
    so a fixture using either still exercises the path.
    """
    qm.record_scorecard({"overall_score": 0.78}, ontology="ent")
    hits = recorder.by("seocho.ontology.scorecard.score")
    assert hits and hits[0][2] == 0.78
