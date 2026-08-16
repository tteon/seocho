"""Keet-style ontology metrics (seocho scorecard improvement)."""
from __future__ import annotations
from seocho.ontology.core import NodeDef, Ontology, P, RelDef
from seocho.ontology.metrics import compute_ontology_metrics


def _onto():
    return Ontology(name="t", nodes={
        "Agent": NodeDef(description="a", properties={"name": P(str)}),
        "Company": NodeDef(description="c", properties={"name": P(str), "ticker": P(str)}, broader=["Agent"]),
        "Person": NodeDef(description="p", properties={"name": P(str)}, broader=["Agent"]),
    }, relationships={"WORKS_AT": RelDef(source="Person", target="Company")})


def test_structural_and_richness_metrics():
    m = compute_ontology_metrics(_onto())
    assert m["classes"] == 3 and m["relationships"] == 1
    assert m["size"] == 3 + 1 + 4          # classes + rels + data props(name,ticker,name,name=4)
    assert m["inheritance_richness"] == round(2 / 3, 3)   # 2 broader edges / 3 classes
    assert m["attribute_richness"] == round(4 / 3, 3)
    # relationship richness = rels / (rels + isa) = 1/(1+2)
    assert m["relationship_richness"] == round(1 / 3, 3)
    assert 0.0 <= m["cohesion"] <= 1.0
    assert "relationship_richness" in m["bands"]


def test_correctness_completeness_vs_reference():
    ref = _onto()
    # M drops Person and adds an off-reference type -> incorrect + incomplete
    m_onto = Ontology(name="m", nodes={
        "Company": NodeDef(properties={"name": P(str)}, broader=["Agent"]),
        "Vendor": NodeDef(properties={"name": P(str)}),   # not in reference
    }, relationships={})
    r = compute_ontology_metrics(m_onto, reference=ref)["vs_reference"]
    assert r["correct"] is False and "Vendor" in r["extra_not_in_reference"]
    assert r["complete"] is False and "Person" in r["missing_from_reference"]
    assert 0.0 <= r["correctness"] <= 1.0 and 0.0 <= r["completeness"] <= 1.0
