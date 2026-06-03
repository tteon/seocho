"""Regression anchor for the deterministic-answer value parser (CLAUDE.md §20).

The structured retrieval lane returns metric `value` as a STRING ("$383.3
billion", "$9,871,649", "$5.23 per share"). The old `_coerce_number` did
`float(text.replace(",",""))`, which returned None for every currency/scale
string → all financial rows were dropped → the deterministic answer never fired
and the pipeline paid an LLM synthesis call (measured: answer_source=
llm_synthesis 10/10). These tests lock the robust parser so that regression
cannot return.
"""
import pytest

from seocho.query.answering import QueryAnswerSynthesizer


def _synth():
    return QueryAnswerSynthesizer(query_strategy=None, llm=None)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$9,871,649", 9_871_649.0),
        ("9,871,649", 9_871_649.0),
        ("$383.3 billion", 383.3e9),
        ("$5.23 per share", 5.23),
        ("12.5%", 12.5),
        ("$1.2 million", 1.2e6),
        ("(1,234)", -1234.0),          # accounting negative
        ("$1,451,594 thousand", 1_451_594e3),
        (1234, 1234.0),
        (12.5, 12.5),
    ],
)
def test_coerce_number_parses_financial_strings(raw, expected):
    got = _synth()._coerce_number(raw)
    assert got is not None
    assert abs(got - expected) < max(1.0, abs(expected) * 1e-9)


@pytest.mark.parametrize("raw", [None, "", "  ", "n/a", "not in context"])
def test_coerce_number_rejects_non_numeric(raw):
    assert _synth()._coerce_number(raw) is None


def test_financial_rows_survive_string_values():
    """The end-to-end symptom: string-valued records must yield financial rows
    (previously zero), which is the precondition for a deterministic answer."""
    records = [
        {"company": "Chipotle", "metric_name": "Total Revenue", "year": "FY2023",
         "value": "$9,871,649", "relationship": "", "supporting_fact": ""},
        {"company": "Chipotle", "metric_name": "Total Revenue", "year": "FY2022",
         "value": "$8,634,652", "relationship": "", "supporting_fact": ""},
    ]
    rows = _synth()._normalize_financial_rows(records)
    assert len(rows) == 2
    assert rows[0]["value"] == 9_871_649.0
    # original string preserved for token-faithful display (number_overlap)
    assert rows[0]["value_display"] == "$9,871,649"
