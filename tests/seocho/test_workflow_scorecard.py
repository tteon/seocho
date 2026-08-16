"""The three-level breakdown must descend, and must not fake what it did not measure.

`score_ontology` grades one artefact. This grades a run: stage (대분류) ->
dimension (중분류) -> finding (소분류), so a reader starts at one number and
descends to a named class or property rather than scanning four dimensions of
the wrong artefact.

The property that matters most here is the distinction between "measured 0" and
"not measured". Collapsing them is how a corpus gets indexed against a grade-B
ontology while the dashboard stays green.
"""

from __future__ import annotations

from seocho.workflow_scorecard import (
    Dimension,
    Finding,
    Stage,
    build_generation_stage,
    build_indexing_stage,
    build_ontology_stage,
    build_retrieval_stage,
    build_workflow_scorecard,
)


def test_an_unmeasured_dimension_is_excluded_rather_than_scored_zero():
    """Zero would make an un-instrumented stage look broken; one, healthy."""
    stage = Stage(name="s", dimensions=[
        Dimension(name="measured", weight=0.5, score=0.8),
        Dimension(name="absent", weight=0.5, score=None),
    ])
    assert stage.score == 0.8, "the absent dimension must not drag the mean"
    assert stage.to_dict()["measured_dimensions"] == 1
    assert stage.to_dict()["total_dimensions"] == 2


def test_a_stage_with_nothing_measured_is_none_not_zero():
    assert Stage(name="s").score is None
    assert Stage(name="s").to_dict()["grade"] == "-"


def test_weights_renormalise_over_what_was_measured():
    """A partial run is scored on what it measured, not punished for the rest."""
    stage = Stage(name="s", dimensions=[
        Dimension(name="a", weight=0.9, score=1.0),
        Dimension(name="b", weight=0.1, score=0.0),
        Dimension(name="c", weight=9.0, score=None),
    ])
    assert abs(stage.score - 0.9) < 1e-9


def test_an_unmeasured_stage_stays_visible():
    """Dropping it would make 'never ran' and 'ran fine' look the same."""
    card = build_workflow_scorecard(Stage(name="ontology",
                                          dimensions=[Dimension("d", 1.0, 1.0)]))
    names = [s["name"] for s in card.to_dict()["stages"]]
    assert names == ["ontology", "indexing", "retrieval", "generation"]
    assert card.stage("retrieval").score is None


def test_the_overall_score_ignores_unmeasured_stages():
    card = build_workflow_scorecard(
        Stage(name="ontology", dimensions=[Dimension("d", 1.0, 0.6)]),
        Stage(name="generation", dimensions=[Dimension("d", 1.0, 1.0)]),
    )
    assert abs(card.score - 0.8) < 1e-9


def test_worst_ranks_major_findings_in_the_weakest_stage_first():
    card = build_workflow_scorecard(
        Stage(name="ontology", dimensions=[
            Dimension("d", 1.0, 0.99, findings=[Finding("minor", "cosmetic")])]),
        Stage(name="indexing", dimensions=[
            Dimension("d", 1.0, 0.20, findings=[Finding("major", "real")])]),
    )
    worst = card.worst()
    assert worst[0]["severity"] == "major" and worst[0]["stage"] == "indexing"


def test_indexing_names_the_property_that_broke_its_vocabulary():
    """A finding must name an element, not report a percentage."""
    rows = [{"nodes": [{"label": "Decision",
                        "properties": {"status": "CURRENT"}}], "relationships": []}]
    stage = build_indexing_stage(rows, allowed_labels=["Decision"],
                                 vocabularies={"Decision.status": ["applied"]})
    vocabulary = next(d for d in stage.dimensions if d.name == "vocabulary_compliance")
    assert vocabulary.score == 0.0
    assert vocabulary.findings[0].element == "Decision.status"


def test_indexing_counts_a_document_that_produced_nothing():
    rows = [{"nodes": [{"label": "X"}], "relationships": []},
            {"nodes": [], "relationships": []}]
    stage = build_indexing_stage(rows)
    yield_dim = next(d for d in stage.dimensions if d.name == "extraction_yield")
    assert yield_dim.score == 0.5
    assert "produced no graph" in yield_dim.findings[0].message


def test_generation_separates_over_refusal_from_under_refusal():
    """The measured failure is bidirectional; one 'refused' number hides both."""
    rows = [
        {"refused": True, "should_refuse": False},   # answerable, refused anyway
        {"refused": False, "should_refuse": True},   # unanswerable, answered anyway
        {"refused": True, "should_refuse": True},
    ]
    stage = build_generation_stage(rows)
    dimension = next(d for d in stage.dimensions if d.name == "refusal_correctness")
    messages = " ".join(f.message for f in dimension.findings)
    assert "refused" in messages and "answered anyway" in messages
    assert abs(dimension.score - 1 / 3) < 1e-9


def test_retrieval_grades_sargability_not_latency():
    stage = build_retrieval_stage([
        {"available": True, "sargable": True, "scans": []},
        {"available": True, "sargable": False, "scans": ["AllNodesScan"]},
    ])
    sarg = next(d for d in stage.dimensions if d.name == "sargability")
    assert sarg.score == 0.5
    assert "grows with the graph" in sarg.findings[0].message


def test_retrieval_with_no_plans_is_unmeasured():
    assert build_retrieval_stage([]).score is None


def test_the_os_contract_gap_is_its_own_dimension():
    """A well-scoring ontology with no identity keys still cannot deduplicate."""
    from seocho import NodeDef, Ontology, P

    ontology = Ontology(name="o", nodes={"A": NodeDef(properties={"n": P(str)})})
    stage = build_ontology_stage(None, ontology)
    contract = next(d for d in stage.dimensions if d.name == "os_contract")
    assert contract.score == 0.0
    assert {f.element for f in contract.findings} == {
        "purpose", "competency_questions", "modelling_decisions",
        "identity", "vocabularies"}
