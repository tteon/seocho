"""Regression for #131 — a delta must follow the year order stated in the
question. `financial_metric_delta` derived start/end from `_ordered_years`,
which always sorts chronologically, so "change from 2023 to 2021" was answered
as 2021 -> 2023 with the sign and increased/decreased direction reversed.
"""

from __future__ import annotations

from seocho.query.answering import QueryAnswerSynthesizer


def _synth() -> QueryAnswerSynthesizer:
    # No llm / strategy needed: the delta branch is fully deterministic.
    return QueryAnswerSynthesizer.__new__(QueryAnswerSynthesizer)


_RECORDS = [
    {"company": "ACME", "metric_name": "revenue", "year": "2021", "value": 100},
    {"company": "ACME", "metric_name": "revenue", "year": "2023", "value": 150},
]


def _intent(years: list[str]) -> dict:
    return {
        "intent": "financial_metric_delta",
        "years": years,
        "anchor_entity": "ACME",
        "metric_name": "revenue",
        "metric_aliases": ["revenue"],
    }


def test_delta_follows_descending_question_phrasing() -> None:
    answer = _synth()._build_financial_answer(
        "how did revenue change from 2023 to 2021",
        _RECORDS,
        _intent(["2023", "2021"]),
    )
    # 2023 (150) -> 2021 (100): a decrease of 50, reported in the stated order.
    assert "decreased" in answer
    assert "from 2023 to 2021" in answer
    assert "increased" not in answer


def test_delta_follows_ascending_question_phrasing() -> None:
    answer = _synth()._build_financial_answer(
        "how did revenue change from 2021 to 2023",
        _RECORDS,
        _intent(["2021", "2023"]),
    )
    assert "increased" in answer
    assert "from 2021 to 2023" in answer
    assert "decreased" not in answer


def test_delta_without_stated_years_defaults_to_chronological() -> None:
    # No years named in the question → fall back to chronological order.
    answer = _synth()._build_financial_answer(
        "how did revenue change",
        _RECORDS,
        _intent([]),
    )
    assert "increased" in answer
    assert "from 2021 to 2023" in answer
