from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology.learning import learn_from_graph, score_learning_task


def _ontology() -> Ontology:
    return Ontology(
        name="work",
        nodes={
            "Organization": NodeDef(properties={"name": P(str)}),
            "Company": NodeDef(properties={"name": P(str)}, broader=["Organization"]),
            "Person": NodeDef(properties={"name": P(str)}),
        },
        relationships={"WORKS_AT": RelDef(source="Person", target="Company")},
    )


def test_learning_report_is_review_only_and_aggregates_terms_relations_and_axioms() -> (
    None
):
    graph = {
        "nodes": [
            {
                "id": "p1",
                "label": "Person",
                "properties": {"name": "Ada", "source_id": "d1"},
            },
            {
                "id": "p2",
                "label": "Person",
                "properties": {"name": "Ada", "source_id": "d2"},
            },
            {
                "id": "c1",
                "label": "Company",
                "properties": {"name": "Acme", "source_id": "d1"},
            },
        ],
        "relationships": [
            {"source": "p1", "type": "WORKS_AT", "target": "c1"},
            {"source": "p2", "type": "WORKS_AT", "target": "c1"},
        ],
    }
    report = learn_from_graph(graph, _ontology(), min_support=2)
    payload = report.to_dict()

    assert payload["promotion"]["status"] == "not_attempted"
    assert [(term.term, term.support) for term in report.terms] == [("Ada", 2)]
    assert report.taxonomy[0].source_type == "Company"
    assert report.relations[0].declared is True
    assert _ontology().nodes["Company"].broader == ["Organization"]


def test_learning_score_never_treats_absent_gold_as_zero() -> None:
    unavailable = score_learning_task(
        "taxonomy", [("Company", "is_a", "Organization")], None
    )
    scored = score_learning_task(
        "relation",
        [("Person", "WORKS_AT", "Company")],
        [("Person", "WORKS_AT", "Company")],
    )

    assert unavailable.status == "unavailable"
    assert unavailable.f1 is None
    assert scored.status == "scored"
    assert scored.f1 == 1.0
