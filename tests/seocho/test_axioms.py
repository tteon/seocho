"""Inductive axiom mining + deductive entailment (seocho-ia4.8/ia4.9)."""

from __future__ import annotations

from seocho.axioms import approve, materialize_entailments, mine_axioms


def _g(nodes, rels):
    return {"nodes": nodes, "relationships": rels}


def test_mine_functional_relationship():
    nodes = [{"id": f"o{i}", "label": "Order"} for i in range(5)] + \
            [{"id": f"c{i}", "label": "Cust"} for i in range(5)]
    rels = [{"source": f"o{i}", "target": f"c{i}", "type": "PLACED_BY"} for i in range(5)]
    ax = mine_axioms(_g(nodes, rels), min_support=3)
    fn = [a for a in ax if a.kind == "functional" and a.subject == "PLACED_BY"]
    assert fn and fn[0].confidence == 1.0


def test_mine_subclass():
    nodes = [{"id": f"m{i}", "label": ["Manager", "Employee"]} for i in range(4)] + \
            [{"id": f"e{i}", "label": "Employee"} for i in range(4)]
    ax = mine_axioms(_g(nodes, []), min_support=3)
    sub = [a for a in ax if a.kind == "subclass"]
    assert any(a.detail == {"child": "Manager", "parent": "Employee"} for a in sub)


def test_mine_disjoint_confidence_tolerates_rare_violation():
    nodes = [{"id": f"p{i}", "label": "Person"} for i in range(10)]
    nodes += [{"id": f"c{i}", "label": "Company"} for i in range(10)]
    # both Person and Company enter the multi-label vocabulary via one mixed node,
    # which is also the rare disjoint violation the axiom must tolerate:
    nodes += [{"id": "mix", "label": ["Person", "Company"]}]
    ax = mine_axioms(_g(nodes, []), min_support=3, min_confidence=0.9)
    dj = [a for a in ax if a.kind == "disjoint"]
    assert any(set(a.detail["labels"]) == {"Person", "Company"} for a in dj)


def test_mine_composition_rule():
    rels = []
    for i in range(6):
        rels += [{"source": f"x{i}", "target": f"y{i}", "type": "R1"},
                 {"source": f"y{i}", "target": f"z{i}", "type": "R2"}]
        if i != 0:
            rels.append({"source": f"x{i}", "target": f"z{i}", "type": "R3"})
    ax = mine_axioms(_g([], rels), min_support=3, mine_rules=True)
    rules = [a for a in ax if a.kind == "rule" and a.detail.get("head") == "R3"]
    assert rules and 0.8 <= rules[0].confidence < 1.0


def test_materialize_detects_functional_violation():
    nodes = [{"id": f"o{i}", "label": "Order"} for i in range(5)]
    rels = [{"source": f"o{i}", "target": f"c{i}", "type": "PLACED_BY"} for i in range(5)]
    rels.append({"source": "o0", "target": "cX", "type": "PLACED_BY"})   # violation
    ax = approve(mine_axioms(_g(nodes, rels), min_support=3, min_confidence=0.8),
                 min_support=3, min_confidence=0.8)
    ent = materialize_entailments(_g(nodes, rels), ax)
    assert any(c["kind"] == "functional_violation" for c in ent["contradictions"])


def test_materialize_adds_entailed_edge():
    rels = []
    for i in range(6):
        rels += [{"source": f"x{i}", "target": f"y{i}", "type": "R1"},
                 {"source": f"y{i}", "target": f"z{i}", "type": "R2"}]
        if i != 0:
            rels.append({"source": f"x{i}", "target": f"z{i}", "type": "R3"})
    ax = approve(mine_axioms(_g([], rels), min_support=3, min_confidence=0.8),
                 min_support=3, min_confidence=0.8)
    ent = materialize_entailments(_g([], rels), ax)
    assert ent["entailed_edges"] >= 1
    assert any(r.get("properties", {}).get("_entailed") == "true"
               for r in ent["relationships"])
