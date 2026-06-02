"""OWL 2 DL consistency reasoner + governance gate (gap-closure items #4/#5).

`reason_consistency` is offline/optional (CLAUDE.md §6.3): it must always return
a well-formed verdict dict and degrade gracefully to ``available=False`` when
owlready2 or a JVM is absent — never raise, never block the gate on its own
absence. `governance_gate` composes structural + lint + consistency; only a
proven inconsistency (consistent is False) blocks.
"""
from __future__ import annotations

import seocho.ontology_governance as gov
from seocho import NodeDef, Ontology, P


def _onto() -> Ontology:
    return Ontology(
        name="x",
        nodes={
            "FinancialMetric": NodeDef(description="A reported figure",
                                       properties={"name": P(str, unique=True)}),
            "Revenue": NodeDef(description="Top-line", broader=["FinancialMetric"],
                               properties={"name": P(str, unique=True)}),
        },
        relationships={},
    )


def test_reason_consistency_returns_well_formed_verdict():
    r = gov.reason_consistency(_onto())
    for key in ("consistent", "unsatisfiable_classes", "available", "reasoner", "error"):
        assert key in r
    # On a box without owlready2/JVM this degrades to available=False and never
    # raises; on a box with a reasoner it reports a real verdict. Either is valid.
    assert isinstance(r["available"], bool)
    if not r["available"]:
        assert r["consistent"] is None and r["error"]


def test_gate_does_not_block_when_reasoner_unavailable(monkeypatch):
    monkeypatch.setattr(gov, "reason_consistency", lambda o: {
        "consistent": None, "unsatisfiable_classes": [], "available": False,
        "reasoner": None, "error": "owlready2 unavailable",
    })
    res = gov.governance_gate(_onto())
    assert res["ok"] is True
    assert res["consistency"]["available"] is False


def test_gate_blocks_on_proven_inconsistency(monkeypatch):
    monkeypatch.setattr(gov, "reason_consistency", lambda o: {
        "consistent": False, "unsatisfiable_classes": ["Revenue"], "available": True,
        "reasoner": "pellet", "error": None,
    })
    res = gov.governance_gate(_onto())
    assert res["ok"] is False
    assert res["consistency"]["consistent"] is False


def test_gate_skips_reasoner_when_disabled():
    res = gov.governance_gate(_onto(), run_reasoner=False)
    assert res["consistency"]["available"] is False
    assert res["ok"] is True  # structural + lint clean


def test_gate_blocks_on_structural_error():
    bad = Ontology(
        name="x",
        nodes={"R": NodeDef(description="d", broader=["Ghost"],
                            properties={"name": P(str, unique=True)})},
        relationships={},
    )
    res = gov.governance_gate(bad, run_reasoner=False)
    assert res["ok"] is False
    assert res["structural"]["ok"] is False
