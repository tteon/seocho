"""Tests for the ``ontology eval-answers`` CLI loader (ADR-0125).

The loader is pure/offline; we also confirm loaded cases feed
``evaluate_answer_accuracy`` over a fake backend (mirrors test_answer_eval.py).
The live CLI handler needs a real backend, so it is not exercised here.
"""

from __future__ import annotations

import json

from seocho.ontology import NodeDef, Ontology, P
from seocho.evaluation import (
    AnswerCase,
    evaluate_answer_accuracy,
    load_answer_cases,
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
        return _Resp('{"facts":[{"label":"Person","name":"x"}],"answer":"a"}')


def test_load_answer_cases_maps_fields(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([
        {"question": "q1", "gold_answer": "yes", "context": "ctx1", "category": "A", "case_id": "c1"},
    ]), encoding="utf-8")
    cases = load_answer_cases(str(path))
    assert len(cases) == 1
    c = cases[0]
    assert isinstance(c, AnswerCase)
    assert c.question == "q1"
    assert c.gold_answer == "yes"
    assert c.context == "ctx1"
    assert c.category == "A"
    assert c.case_id == "c1"


def test_load_answer_cases_defaults_optionals(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"question": "q", "gold_answer": "no"}]), encoding="utf-8")
    (c,) = load_answer_cases(str(path))
    assert c.question == "q" and c.gold_answer == "no"
    assert c.context == "" and c.category == "" and c.case_id == ""


def test_load_answer_cases_rejects_non_list(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"question": "q"}), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        load_answer_cases(str(path))


def test_loaded_cases_evaluate_with_fake_backend(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([
        {"question": "q1", "gold_answer": "yes", "context": "ctx", "category": "A", "case_id": "1"},
        {"question": "q2", "gold_answer": "no", "context": "ctx", "category": "B", "case_id": "2"},
    ]), encoding="utf-8")
    cases = load_answer_cases(str(path))
    rep = evaluate_answer_accuracy(_FakeBackend(), _onto("Person"), cases, workers=1)
    assert rep.n_scored == 2
    assert rep.accuracy == 0.5
    assert rep.by_category == {"A": 1.0, "B": 0.0}
    assert rep.errors == 0
