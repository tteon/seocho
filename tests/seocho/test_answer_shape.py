"""AnswerShape classification + terse-directive contract (opik-derived).

Pins the rule-based classifier and the synthesis-directive mapping that
the FinDER T2 A/B relies on. Motivated by the baseline finding: retrieval
is correct (contains=1.0) but verbose synthesis sinks exact-match/token-F1;
AnswerShape steers terse output.
"""

from __future__ import annotations

import pytest

from seocho.query.answer_shape import (
    AnswerShape,
    answer_shape_enabled,
    classify_answer_shape,
    terse_directive,
)


def test_answer_shape_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adopted 2026-06-03: AnswerShape is default-on (opt-out)."""
    monkeypatch.delenv("SEOCHO_ANSWER_SHAPE", raising=False)
    assert answer_shape_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "FALSE", "No"])
def test_answer_shape_opt_out(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("SEOCHO_ANSWER_SHAPE", val)
    assert answer_shape_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", ""])
def test_answer_shape_stays_on_for_truthy_or_empty(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    # empty string keeps default-on (only explicit 0/false/no disables)
    monkeypatch.setenv("SEOCHO_ANSWER_SHAPE", val)
    assert answer_shape_enabled() is True


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What was Microsoft's total revenue in fiscal year 2023?", AnswerShape.SCALAR_METRIC),
        ("How much did NVIDIA pay in dividends during fiscal year 2023?", AnswerShape.SCALAR_METRIC),
        ("What was the value of Meta's Cambridge Analytica settlement?", AnswerShape.SCALAR_METRIC),
        ("Where is Apple Inc. headquartered?", AnswerShape.LOCATION),
        ("Who is the Chair of Alphabet's Board of Directors?", AnswerShape.ENTITY_NAME),
        ("Why did revenue decline in 2023?", AnswerShape.EXPLANATION),
        ("List all subsidiaries of Acme.", AnswerShape.ENTITY_LIST),
    ],
)
def test_classify_answer_shape_rules(question: str, expected: AnswerShape) -> None:
    assert classify_answer_shape(question) == expected


def test_classify_empty_is_unknown() -> None:
    assert classify_answer_shape("") == AnswerShape.UNKNOWN
    assert classify_answer_shape("   ") == AnswerShape.UNKNOWN


def test_terse_directive_present_for_value_shapes() -> None:
    for shape in (AnswerShape.SCALAR_METRIC, AnswerShape.ENTITY_NAME, AnswerShape.LOCATION, AnswerShape.ENTITY_LIST):
        d = terse_directive(shape)
        assert d and "ONLY" in d


def test_terse_directive_none_for_prose_shapes() -> None:
    # explanation/unknown must NOT inject a directive — prose is correct,
    # so synthesis stays at baseline behavior.
    assert terse_directive(AnswerShape.EXPLANATION) is None
    assert terse_directive(AnswerShape.UNKNOWN) is None


def test_synthesize_appends_directive_only_when_shape_set() -> None:
    """QueryAnswerSynthesizer.synthesize stays baseline when answer_shape is
    None, and appends the directive to the user prompt when a value-shape is
    passed. Uses a fake llm + strategy to capture the rendered prompt."""
    from seocho.query.answering import QueryAnswerSynthesizer

    captured = {}

    class FakeStrategy:
        def render_answer(self, question, records_json):
            return ("sys", f"answer for: {question}")

    class FakeResp:
        text = "ok"

    class FakeLLM:
        def complete(self, *, system, user, **kw):
            captured["user"] = user
            return FakeResp()

    synth = QueryAnswerSynthesizer.__new__(QueryAnswerSynthesizer)
    synth.llm = FakeLLM()
    synth.query_strategy = FakeStrategy()

    # baseline: no shape → no directive
    synth.synthesize("What was revenue?", [], answer_shape=None)
    assert "Output format requirement" not in captured["user"]

    # treatment: scalar_metric → terse directive appended
    synth.synthesize("What was revenue?", [], answer_shape=AnswerShape.SCALAR_METRIC)
    assert "Output format requirement" in captured["user"]
    assert "ONLY the value" in captured["user"]

    # explanation shape → no directive even though shape is set
    captured.clear()
    synth.synthesize("Why did it drop?", [], answer_shape=AnswerShape.EXPLANATION)
    assert "Output format requirement" not in captured.get("user", "")
