"""Tests for the domain-adaptive guardrail selector (ADR-0122 follow-up)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_scorecard import CorpusProfile, build_corpus_profile
from seocho.guardrail_selector import (
    load_corpus_profile,
    numeric_intensity,
    select_guardrail,
    select_per_domain,
)


def _lean() -> Ontology:
    return Ontology("lean", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
    })


def _rich() -> Ontology:
    return Ontology("rich", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
        "Person": NodeDef(description="A person.", properties={"name": P(str, unique=True)}),
        "Regulation": NodeDef(description="A rule.", properties={"name": P(str, unique=True)}),
        "Risk": NodeDef(description="A risk.", properties={"name": P(str, unique=True)}),
        "LegalIssue": NodeDef(description="A legal issue.", properties={"name": P(str, unique=True)}),
    })


CANDIDATES = {"lean": _lean(), "rich": _rich()}

# entity-heavy corpus: people, regulations, risks, legal issues
_ENTITY_CORPUS = build_corpus_profile([
    {"nodes": [{"label": "Person"}, {"label": "Regulation"}, {"label": "Governance"}]},
    {"nodes": [{"label": "Risk"}, {"label": "LegalIssue"}, {"label": "Person"}]},
    {"nodes": [{"label": "Committee"}, {"label": "Regulation"}]},
])

# numeric-heavy corpus: metrics/amounts dominate
_NUMERIC_CORPUS = build_corpus_profile([
    {"nodes": [{"label": "FinancialMetric"}, {"label": "Revenue"}, {"label": "EBITDA"}]},
    {"nodes": [{"label": "FinancialMetric"}, {"label": "NetIncome"}, {"label": "Margin"}]},
    {"nodes": [{"label": "Company"}, {"label": "FinancialMetric"}]},
])


def test_numeric_intensity_discriminates():
    assert numeric_intensity(_NUMERIC_CORPUS) > 0.6
    assert numeric_intensity(_ENTITY_CORPUS) < 0.2


def test_entity_corpus_selects_rich():
    rec = select_guardrail(CANDIDATES, _ENTITY_CORPUS)
    assert rec.chosen == "rich"
    assert rec.domain_kind == "entity"
    assert rec.candidate_scores["rich"]["corpus_coverage"] >= rec.candidate_scores["lean"]["corpus_coverage"]


def test_numeric_corpus_selects_lean_and_advises_validation():
    rec = select_guardrail(CANDIDATES, _NUMERIC_CORPUS)
    assert rec.domain_kind == "numeric"
    # lean is within epsilon of best coverage on a numeric corpus → chosen
    assert rec.chosen == "lean"
    assert any("VALIDATION" in a or "validation" in a for a in rec.advisories)


def test_numeric_corpus_picks_richer_only_if_coverage_much_better():
    # if lean cannot cover the numeric corpus well, the selector must not force lean
    onlymetric_lean = Ontology("om", nodes={"Company": NodeDef(description="c", properties={"name": P(str, unique=True)})})
    cands = {"tiny": onlymetric_lean, "rich": _rich()}
    rec = select_guardrail(cands, _NUMERIC_CORPUS, coverage_epsilon=0.05)
    # tiny has near-zero coverage; rich covers FinancialMetric/Company → rich wins despite numeric
    assert rec.chosen == "rich"


def test_select_per_domain_maps_each():
    recs = select_per_domain({"gov": _ENTITY_CORPUS, "fin": _NUMERIC_CORPUS}, CANDIDATES)
    assert recs["gov"].chosen == "rich"
    assert recs["fin"].chosen == "lean"


def test_load_corpus_profile_shapes():
    a = load_corpus_profile({"Person": 3, "Company": 1})
    assert a.label_frequencies["Person"] == 3
    b = load_corpus_profile({"corpus_profile": {"label_frequencies": {"Risk": 2}, "doc_count": 1}})
    assert b.label_frequencies["Risk"] == 2 and b.doc_count == 1
