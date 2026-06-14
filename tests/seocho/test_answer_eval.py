"""Tests for the answer-accuracy evaluation surface (ADR-0122/0123)."""

from __future__ import annotations

import json

from seocho.ontology import NodeDef, Ontology, P
from seocho.evaluation import (
    AnswerCase,
    compare_guardrails_by_answer,
    evaluate_answer_accuracy,
)


def _onto(*labels) -> Ontology:
    return Ontology("o", nodes={l: NodeDef(description=f"{l}.", properties={"name": P(str, unique=True)}) for l in labels})


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeBackend:
    """Answer call → fixed facts+answer; judge call (system mentions 'grade') →
    correct iff the gold answer is 'yes'."""
    model = "DeepSeek-V3.1"

    def complete(self, *, system, user, **kw):
        if "grade" in system.lower():
            correct = "gold: yes" in user.lower()
            return _Resp(json.dumps({"correct": correct}))
        return _Resp('{"facts":[{"label":"Person","name":"x"},{"label":"OOV","name":"y"}],"answer":"a"}')


def _cases():
    return [
        AnswerCase(question="q1", gold_answer="yes", context="ctx", category="A", case_id="1"),
        AnswerCase(question="q2", gold_answer="no", context="ctx", category="B", case_id="2"),
    ]


def test_answer_accuracy_overall_and_by_category():
    rep = evaluate_answer_accuracy(_FakeBackend(), _onto("Person"), _cases(), workers=1)
    assert rep.n_scored == 2
    assert rep.accuracy == 0.5
    assert rep.by_category == {"A": 1.0, "B": 0.0}
    assert rep.by_category_n == {"A": 1, "B": 1}
    assert rep.errors == 0


def test_label_conformance_recorded():
    rep = evaluate_answer_accuracy(_FakeBackend(), _onto("Person"), _cases(), workers=1)
    # facts were Person (in ontology) + OOV (not) → conformance 0.5
    assert all(r["label_conformance"] == 0.5 for r in rep.results if "label_conformance" in r)


def test_errors_excluded_from_scoring():
    class _Boom:
        model = "x"
        def complete(self, **kw):
            raise RuntimeError("boom")  # non-retryable
    rep = evaluate_answer_accuracy(_Boom(), _onto("Person"), _cases(), workers=1)
    assert rep.errors == 2 and rep.n_scored == 0 and rep.accuracy == 0.0


def test_compare_guardrails_by_answer():
    reps = compare_guardrails_by_answer(
        _FakeBackend(), {"lean": _onto("Person"), "rich": _onto("Person", "Company")}, _cases(), workers=1)
    assert set(reps) == {"lean", "rich"}
    assert reps["lean"].accuracy == 0.5 and reps["rich"].accuracy == 0.5


def test_to_dict_shape():
    rep = evaluate_answer_accuracy(_FakeBackend(), _onto("Person"), _cases(), workers=1)
    d = rep.to_dict()
    assert set(d) >= {"n_scored", "accuracy", "by_category", "by_category_n", "errors", "results"}
