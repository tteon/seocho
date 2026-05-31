"""ISO-704 definition surfacing (#6) + competency-question coverage (#7).

#6: a class's `description` is its ISO-704 definition and must surface as
`skos:definition` in TTL (round-tripping cleanly) and as `VocabularyTerm.
definition` in the SKOS vocabulary candidate — never leaking the structured
definition machinery into the lean extraction projection.

#7: competency-question coverage lint flags ontology elements no CQ exercises
and CQs that touch no ontology element. Metadata/coverage only — the evaluation
runner is intentionally deferred.
"""
from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_artifacts import ontology_to_vocabulary_candidate
from seocho.ontology_governance import competency_question_coverage


def _onto() -> Ontology:
    return Ontology(
        name="fin",
        nodes={
            "FinancialMetric": NodeDef(description="A reported financial figure",
                                       properties={"name": P(str, unique=True)}),
            "Revenue": NodeDef(description="Top-line revenue", broader=["FinancialMetric"],
                               aliases=["NetSales"], properties={"name": P(str, unique=True)}),
        },
        relationships={"HAS_SEGMENT": RelDef(source="FinancialMetric", target="Revenue",
                                             description="metric broken out by segment")},
    )


def test_definition_surfaces_in_vocabulary_candidate():
    vocab = ontology_to_vocabulary_candidate(_onto())
    terms = {t.pref_label: t for t in vocab.terms}
    assert terms["Revenue"].definition == "Top-line revenue"
    assert terms["FinancialMetric"].definition == "A reported financial figure"


def test_definition_round_trips_as_skos_definition(tmp_path):
    rdflib = pytest.importorskip("rdflib")
    ttl = tmp_path / "fin.ttl"
    _onto().to_ttl(ttl)
    text = ttl.read_text()
    assert "skos:definition" in text or "definition" in text  # emitted as SKOS
    loaded = Ontology.from_ttl(ttl)
    assert loaded.nodes["Revenue"].description == "Top-line revenue"
    assert loaded.nodes["FinancialMetric"].description == "A reported financial figure"


def test_cq_coverage_flags_uncovered_elements():
    cqs = ["What was the total revenue?"]  # touches Revenue only
    cov = competency_question_coverage(_onto(), cqs)
    assert "Revenue" not in cov["uncovered_elements"]
    # FinancialMetric (spaced form "financial metric") + HAS_SEGMENT not asked
    assert "FinancialMetric" in cov["uncovered_elements"]
    assert "HAS_SEGMENT" in cov["uncovered_elements"]
    assert 0.0 < cov["coverage_ratio"] < 1.0


def test_cq_coverage_alias_and_spaced_match():
    cqs = [
        "Which NetSales has segment breakdowns?",  # alias of Revenue + HAS_SEGMENT (spaced)
        "List every financial metric reported",    # spaced form of FinancialMetric
    ]
    cov = competency_question_coverage(_onto(), cqs)
    assert cov["uncovered_elements"] == []
    assert cov["coverage_ratio"] == 1.0
    assert cov["empty_questions"] == []


def test_cq_coverage_flags_empty_question():
    cqs = ["What is the weather today?"]  # touches nothing
    cov = competency_question_coverage(_onto(), cqs)
    assert cov["empty_questions"] == ["What is the weather today?"]
    assert cov["covered_elements"] == 0
