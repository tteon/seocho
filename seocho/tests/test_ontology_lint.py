"""Tests for the FIBO/ISO-704 hygiene linter (gap-closure plan item #2).

Offline, no services — pure model walk over an Ontology.
"""
from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_governance import lint_ontology, check_ontology


def _clean() -> Ontology:
    return Ontology(
        name="finance",
        nodes={
            "FinancialMetric": NodeDef(description="A reported financial figure",
                                       properties={"name": P(str, unique=True)}),
            "Revenue": NodeDef(description="Top-line revenue", broader=["FinancialMetric"],
                               properties={"name": P(str, unique=True)}),
        },
        relationships={"REPORTED_METRIC": RelDef(source="FinancialMetric", target="Revenue",
                                                 description="entity reported a metric")},
    )


def test_clean_ontology_passes_lint():
    res = lint_ontology(_clean())
    assert res["ok"] is True and res["errors"] == []


def test_missing_definition_warns():
    o = Ontology(name="x", nodes={"Thing": NodeDef(properties={"name": P(str, unique=True)})},
                 relationships={})
    res = lint_ontology(o)
    assert any(f["check"] == "missing_definition" for f in res["warnings"])


def test_naming_convention_warns():
    o = Ontology(name="x",
                 nodes={"financial_metric": NodeDef(description="d", properties={"name": P(str, unique=True)})},
                 relationships={})
    res = lint_ontology(o)
    assert any(f["check"] == "naming" for f in res["warnings"])


def test_dangling_broader_is_error():
    o = Ontology(name="x",
                 nodes={"Revenue": NodeDef(description="d", broader=["Nonexistent"],
                                           properties={"name": P(str, unique=True)})},
                 relationships={})
    res = lint_ontology(o)
    assert res["ok"] is False
    assert any(f["check"] == "broader_target" for f in res["errors"])


def test_broader_cycle_is_error():
    o = Ontology(name="x",
                 nodes={"A": NodeDef(description="d", broader=["B"], properties={"name": P(str, unique=True)}),
                        "B": NodeDef(description="d", broader=["A"], properties={"name": P(str, unique=True)})},
                 relationships={})
    res = lint_ontology(o)
    assert res["ok"] is False
    assert any(f["check"] == "broader_cycle" for f in res["errors"])


def test_duplicate_class_and_rel_label_is_error():
    o = Ontology(name="x",
                 nodes={"Owns": NodeDef(description="d", properties={"name": P(str, unique=True)}),
                        "Person": NodeDef(description="d", properties={"name": P(str, unique=True)})},
                 relationships={"Owns": RelDef(source="Person", target="Owns", description="d")})
    res = lint_ontology(o)
    assert any(f["check"] == "duplicate_label" for f in res["errors"])


def test_check_ontology_surfaces_lint_warnings_without_flipping_ok():
    # clean structure but missing definitions -> warnings, still ok
    o = Ontology(name="x", nodes={"Thing": NodeDef(properties={"name": P(str, unique=True)})},
                 relationships={})
    res = check_ontology(o)
    assert res.ok is True
    assert any("hygiene:" in w for w in res.warnings)
    assert res.stats["hygiene_warning_count"] >= 1
