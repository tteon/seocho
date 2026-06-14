"""Tests for the ontology quality scorecard (seocho.ontology_scorecard)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_scorecard import (
    DEFAULT_WEIGHTS,
    WEIGHT_PROFILES,
    CorpusProfile,
    OntologyScorecard,
    build_corpus_profile,
    score_ontology,
)


def _well_formed_ontology() -> Ontology:
    """A small, deliberately healthy ontology: defined, identified, constrained,
    connected taxonomy."""
    return Ontology(
        "people-orgs",
        version="1.0.0",
        nodes={
            "Agent": NodeDef(description="Anything that can act."),
            "Person": NodeDef(
                description="A human being.",
                broader=["Agent"],
                properties={"name": P(str, unique=True, description="Full name.")},
            ),
            "Organization": NodeDef(
                description="A structured group of people.",
                broader=["Agent"],
                properties={"name": P(str, unique=True, description="Legal name.")},
            ),
        },
        relationships={
            "WORKS_AT": RelDef(
                source="Person",
                target="Organization",
                cardinality="MANY_TO_ONE",
                description="Employment relationship.",
            ),
        },
    )


def _defective_ontology() -> Ontology:
    """A deliberately weak ontology: flat (no broader), an orphan class, classes
    without identity or definition, an untyped relationship."""
    return Ontology(
        "messy",
        version="bad-version",
        nodes={
            "Thing": NodeDef(),  # no description, no identity
            "Widget": NodeDef(description="A widget."),  # no identity
            "Gadget": NodeDef(description="A gadget.", properties={"sku": P(str, unique=True)}),
            "Floater": NodeDef(description="Connected to nothing."),  # orphan
            "Person": NodeDef(description="A person.", properties={"name": P(str, unique=True)}),
            "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        },
        relationships={
            # untyped endpoint (target = Any) — constrains no traversal
            "RELATES_TO": RelDef(source="Person", target="Any", description="Vague link."),
        },
    )


def test_well_formed_ontology_scores_high_and_not_blocking():
    card = score_ontology(_well_formed_ontology())
    assert isinstance(card, OntologyScorecard)
    assert not card.blocking
    assert card.overall_score >= 0.8
    assert card.grade in {"A", "B"}
    # taxonomy is connected: no orphans
    tax = card.dimension("taxonomy_health")
    assert tax is not None
    assert tax.stats["orphan_count"] == 0


def test_defective_ontology_scores_low_and_surfaces_weak_points():
    card = score_ontology(_defective_ontology())
    assert card.overall_score < 0.8
    # Floater is disconnected → reported as an orphan
    tax = card.dimension("taxonomy_health")
    assert "Floater" in tax.stats["orphans"]
    # flatness flagged (6 classes, zero broader edges)
    assert any("Flat" in f or "flat" in f for f in tax.findings)
    # classes without identity are surfaced
    defi = card.dimension("definitional_completeness")
    assert "Thing" in defi.stats["classes_without_identity"]
    assert "Widget" in defi.stats["classes_without_identity"]
    # untyped relationship endpoint penalised
    constraint = card.dimension("constraint_richness")
    assert constraint.stats["typed_endpoint_ratio"] < 1.0
    # weak points are present and sorted by severity
    assert card.weak_points
    severities = [wp.severity for wp in card.weak_points]
    rank = {"blocking": 0, "major": 1, "minor": 2}
    assert severities == sorted(severities, key=lambda s: rank.get(s, 3))


def test_structural_error_is_blocking_and_caps_grade():
    # duplicate label used as BOTH a class and a relationship → lint ERROR
    onto = Ontology(
        "dup",
        nodes={"Foo": NodeDef(description="A foo.", properties={"id": P(str, unique=True)})},
        relationships={"Foo": RelDef(source="Foo", target="Foo", description="self.")},
    )
    card = score_ontology(onto)
    assert card.blocking
    # blocking caps the grade at D even if other dimensions are fine
    assert card.grade in {"D", "F"}
    assert any(wp.severity == "blocking" for wp in card.weak_points)


def test_functional_coverage_skipped_without_competency_questions():
    card = score_ontology(_well_formed_ontology())
    assert card.dimension("functional_coverage") is None
    assert not card.stats["competency_questions_supplied"]
    # a weak point notes the skipped functional validation
    assert any("functional" in wp.message.lower() for wp in card.weak_points)


def test_functional_coverage_with_dict_competency_questions():
    onto = _well_formed_ontology()
    cqs = [
        {"id": "cq1", "question": "Where does a person work?", "requires": ["Person", "WORKS_AT", "Organization"]},
        {"id": "cq2", "question": "What is the budget?", "requires": ["Budget"]},  # impossible
    ]
    card = score_ontology(onto, competency_questions=cqs)
    fc = card.dimension("functional_coverage")
    assert fc is not None
    assert fc.stats["expressible_ratio"] == 0.5
    assert any("impossible" in f for f in fc.findings)


def test_functional_coverage_with_string_competency_questions():
    onto = _well_formed_ontology()
    card = score_ontology(onto, competency_questions=["Where does a Person work at an Organization?"])
    fc = card.dimension("functional_coverage")
    assert fc is not None
    assert "element_coverage_ratio" in fc.stats


def test_to_dict_round_trips_structure():
    card = score_ontology(_well_formed_ontology())
    payload = card.to_dict()
    assert payload["ontology_name"] == "people-orgs"
    assert "dimensions" in payload and payload["dimensions"]
    assert "weak_points" in payload
    assert set(payload.keys()) >= {
        "overall_score", "grade", "blocking", "dimensions", "weak_points", "stats"
    }


def test_custom_weights_renormalise():
    onto = _well_formed_ontology()
    card = score_ontology(onto, weights={"structural_integrity": 1.0, "taxonomy_health": 0.0,
                                         "definitional_completeness": 0.0, "constraint_richness": 0.0})
    # with all weight on a clean structural dimension, overall ~ structural score
    structural = card.dimension("structural_integrity")
    assert abs(card.overall_score - structural.score) < 1e-9


def test_default_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_all_weight_profiles_sum_to_one():
    for name, w in WEIGHT_PROFILES.items():
        assert abs(sum(w.values()) - 1.0) < 1e-9, name


def _sparse_onto() -> Ontology:
    return Ontology("sparse", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
    })


def _rich_onto() -> Ontology:
    return Ontology("rich", nodes={
        "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
        "Person": NodeDef(description="A person.", properties={"name": P(str, unique=True)}),
        "Regulation": NodeDef(description="A rule.", aliases=["Rule"], properties={"name": P(str, unique=True)}),
        "Risk": NodeDef(description="A risk.", properties={"name": P(str, unique=True)}),
    })


# corpus that needs people, regulations, risks — not just companies/metrics
_CORPUS = build_corpus_profile([
    {"nodes": [{"label": "Company"}, {"label": "Person"}, {"label": "Regulation"}]},
    {"nodes": [{"label": "Risk"}, {"label": "Person"}, {"label": "FinancialMetric"}]},
    {"nodes": [{"label": "Regulation"}, {"label": "Risk"}]},
], source="test")


def test_build_corpus_profile_counts_labels():
    assert _CORPUS.label_frequencies["Person"] == 2
    assert _CORPUS.label_frequencies["Regulation"] == 2
    assert _CORPUS.doc_count == 3


def test_corpus_coverage_sparse_low_rich_high():
    sparse = score_ontology(_sparse_onto(), corpus_profile=_CORPUS)
    rich = score_ontology(_rich_onto(), corpus_profile=_CORPUS)
    cs = sparse.dimension("corpus_coverage")
    cr = rich.dimension("corpus_coverage")
    assert cs is not None and cr is not None
    assert cr.score > cs.score  # rich covers more of what the corpus needs
    # sparse surfaces the missing classes as weak points
    missing = {wp.target for wp in sparse.weak_points if wp.dimension == "corpus_coverage"}
    assert {"Person", "Regulation", "Risk"} & missing


def test_corpus_coverage_skipped_without_profile():
    card = score_ontology(_rich_onto())
    assert card.dimension("corpus_coverage") is None
    assert any(wp.dimension == "corpus_coverage" for wp in card.weak_points)


def test_guardrail_profile_ranks_rich_above_sparse_on_corpus():
    # the divergence fix: with the guardrail profile + corpus, the rich (flat but
    # corpus-adequate) ontology should outrank the sparse one overall.
    sparse = score_ontology(_sparse_onto(), corpus_profile=_CORPUS, profile="guardrail")
    rich = score_ontology(_rich_onto(), corpus_profile=_CORPUS, profile="guardrail")
    assert rich.overall_score > sparse.overall_score
    assert rich.stats["weight_profile"] == "guardrail"


def test_profile_changes_weighting():
    onto = _rich_onto()
    guard = score_ontology(onto, corpus_profile=_CORPUS, profile="guardrail")
    tax = score_ontology(onto, corpus_profile=_CORPUS, profile="taxonomy")
    # taxonomy dimension carries more weight under the taxonomy profile
    assert guard.dimension("taxonomy_health").weight < tax.dimension("taxonomy_health").weight
    assert guard.dimension("corpus_coverage").weight > tax.dimension("corpus_coverage").weight
