"""A type mismatch must score 0, not half a point.

`score_extraction`'s type check gave `+0.5` "partial credit" to anything that
fell through its if/elif chain. Two consequences, pulling in opposite directions.

A genuine mismatch — `age` declared INTEGER, extracted as `"thirty"` — kept half
its credit, so a node with every property mis-typed still scored 0.5 on type
correctness. The indexing quality-retry gate compares the overall score against
a threshold, so a malformed extraction stayed above it and the gate never fired.

And `LIST`/`POINT` had no branch of their own, so a *correctly* typed list value
fell into that same else and was docked half a point for being right. The two
halves are the same bug: the else branch was doing the work of a type check.
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P, PropertyType


@pytest.fixture
def person_ontology():
    return Ontology(
        name="test",
        description="Test ontology",
        nodes={
            "Person": NodeDef(
                description="A person",
                properties={"name": P(str, unique=True), "age": P(int)},
            ),
        },
    )


def test_type_mismatch_scores_zero_not_partial(person_ontology):
    """name is right, age is wrong -> 1 of 2. Previously 1.5 of 2 = 0.75."""
    data = {
        "nodes": [
            {"id": "p1", "label": "Person",
             "properties": {"name": "Alice", "age": "thirty"}},
        ],
        "relationships": [],
    }
    scores = person_ontology.score_extraction(data)
    assert scores["nodes"][0]["details"]["type_correctness"] == 0.5


def test_all_wrong_types_score_zero(person_ontology):
    """The case the retry gate depends on: nothing correct must read as nothing."""
    data = {
        "nodes": [
            {"id": "p1", "label": "Person",
             "properties": {"name": 123, "age": "not-a-number"}},
        ],
        "relationships": [],
    }
    scores = person_ontology.score_extraction(data)
    assert scores["nodes"][0]["details"]["type_correctness"] == 0.0


def test_correct_types_still_score_full(person_ontology):
    """The fix must not make the scorer stricter about values that are right."""
    data = {
        "nodes": [
            {"id": "p1", "label": "Person",
             "properties": {"name": "Alice", "age": 30}},
        ],
        "relationships": [],
    }
    scores = person_ontology.score_extraction(data)
    assert scores["nodes"][0]["details"]["type_correctness"] == 1.0


@pytest.mark.parametrize("declared,good,bad", [
    (PropertyType.LIST, ["a", "b"], "a,b"),
    (PropertyType.POINT, {"x": 1.0, "y": 2.0}, "1.0,2.0"),
])
def test_list_and_point_are_credited_when_correct(declared, good, bad):
    """Both were unhandled and fell into the partial-credit branch."""
    onto = Ontology(
        name="t",
        nodes={"Doc": NodeDef(description="d", properties={"v": P(declared)})},
    )

    def score(value):
        return onto.score_extraction({
            "nodes": [{"id": "d1", "label": "Doc", "properties": {"v": value}}],
            "relationships": [],
        })["nodes"][0]["details"]["type_correctness"]

    assert score(good) == 1.0
    assert score(bad) == 0.0
