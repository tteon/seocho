"""Tests for the ambiguity proposal engine — Phase 2 (ADR-0128)."""

from __future__ import annotations

import json

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import (
    apply_mapping_spec,
    propose_mappings,
    proposals_to_mapping_spec,
)


def _onto() -> Ontology:
    return Ontology("biz", version="1.0.0", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "Concept": NodeDef(description="A concept."),
    })


_CLUSTERS = [
    {"surface": "Regulation", "frequency": 12, "signals": {"oov": 12}, "candidate_labels": [], "examples": ["Basel III..."]},
    {"surface": "Adj. EBITDA", "frequency": 7, "signals": {"oov": 7}, "candidate_labels": [], "examples": []},
    {"surface": "junk", "frequency": 1, "signals": {"oov": 1}, "candidate_labels": [], "examples": []},
]


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeBackend:
    model = "DeepSeek-V3.1"

    def complete(self, *, system, user, **kw):
        return _Resp(json.dumps({"proposals": [
            {"surface": "Regulation", "action": "new_class", "target": "Regulation",
             "parent": "Concept", "description": "A regulatory rule.", "confidence": 0.9, "rationale": "recurring"},
            {"surface": "Adj. EBITDA", "action": "alias", "target": "Company",
             "confidence": 0.4, "rationale": "metric alias (toy)"},
            {"surface": "junk", "action": "ignore", "confidence": 0.8, "rationale": "noise"},
        ]}))


def test_propose_mappings_parses_and_scores():
    props = propose_mappings(_CLUSTERS, _onto(), backend=_FakeBackend())
    by_surface = {p.surface: p for p in props}
    assert by_surface["Regulation"].action == "new_class"
    # new_class for a high-frequency surface in the cluster corpus → positive coverage lift
    assert by_surface["Regulation"].predicted_coverage_delta is not None
    assert by_surface["Regulation"].predicted_coverage_delta > 0
    # ranked: the biggest predicted lift comes first
    assert props[0].surface == "Regulation"


def test_proposals_to_spec_filters_and_round_trips():
    props = propose_mappings(_CLUSTERS, _onto(), backend=_FakeBackend())
    spec = proposals_to_mapping_spec(props, min_confidence=0.5)
    surfaces = {m["surface"] for m in spec["mappings"]}
    assert "Regulation" in surfaces           # conf 0.9 kept
    assert "Adj. EBITDA" not in surfaces      # conf 0.4 filtered
    assert "junk" not in surfaces             # ignore dropped
    # the spec applies cleanly and adds the new class
    new_onto = apply_mapping_spec(_onto(), spec)
    assert "Regulation" in new_onto.nodes
    assert new_onto.nodes["Regulation"].broader == ["Concept"]


def test_empty_clusters_returns_empty():
    assert propose_mappings([], _onto(), backend=_FakeBackend()) == []
