"""Tests for learning the numeric-intensity threshold (ADR-0126)."""

from __future__ import annotations

from seocho.ontology_scorecard import build_corpus_profile
from seocho.guardrail_selector import (
    DomainObservation,
    calibrate_numeric_threshold,
    select_guardrail,
)
from seocho.ontology import NodeDef, Ontology, P


def test_separable_observations_give_perfect_threshold():
    # entity domains (low ni, rich helped) vs numeric domains (high ni, rich hurt)
    obs = [
        DomainObservation("Governance", 0.05, +0.67),
        DomainObservation("Legal", 0.15, +0.42),
        DomainObservation("Financials", 0.80, -0.08),
        DomainObservation("Shareholder return", 0.90, -0.17),
    ]
    out = calibrate_numeric_threshold(obs)
    assert out["accuracy"] == 1.0
    assert 0.15 < out["threshold"] < 0.80  # boundary lands between the clusters
    assert out["n"] == 4


def test_empty_returns_default():
    out = calibrate_numeric_threshold([], default=0.42)
    assert out["threshold"] == 0.42 and out["accuracy"] is None and out["n"] == 0


def test_noisy_picks_best_possible():
    obs = [
        DomainObservation("a", 0.2, +0.3),
        DomainObservation("b", 0.4, -0.1),   # overlaps — entity-ish ni but hurt
        DomainObservation("c", 0.6, +0.2),   # overlaps — numeric-ish ni but helped
        DomainObservation("d", 0.8, -0.2),
    ]
    out = calibrate_numeric_threshold(obs)
    assert 0.0 <= out["accuracy"] <= 1.0
    assert out["accuracy"] >= 0.75  # best separator still classifies >= 3/4


def _lean():
    return Ontology("lean", nodes={"Company": NodeDef(description="c", properties={"name": P(str, unique=True)})})


def _rich():
    return Ontology("rich", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
        "Person": NodeDef(description="p", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="m", properties={"name": P(str, unique=True)}),
    })


def test_calibrated_threshold_flips_decision_at_boundary():
    # a corpus with numeric_intensity ~0.5 (one metric, one entity mention)
    corpus = build_corpus_profile([{"nodes": [{"label": "FinancialMetric"}, {"label": "Person"}]}])
    cands = {"lean": _lean(), "rich": _rich()}
    ni = select_guardrail(cands, corpus).numeric_intensity
    # threshold just above ni → corpus treated as entity → rich
    hi = select_guardrail(cands, corpus, numeric_threshold=ni + 0.01)
    # threshold just below ni → corpus treated as numeric → lean preferred when coverage close
    lo = select_guardrail(cands, corpus, numeric_threshold=max(0.0, ni - 0.01))
    # threshold above ni → not classified numeric (rich-leaning); below → numeric (lean-leaning)
    assert hi.domain_kind != "numeric"
    assert lo.domain_kind == "numeric"
