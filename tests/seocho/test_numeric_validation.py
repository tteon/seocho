"""Tests for soft, precision-first numeric validation (ADR-0127)."""

from __future__ import annotations

from seocho.numeric_validation import (
    NumericFact,
    reconcile,
    validate_numeric_facts,
)


def test_clean_facts_not_flagged():
    # the ADR-0119 precision fix: a well-formed fact must NOT be flagged
    res = validate_numeric_facts([
        {"name": "total revenue", "value": "1,234.5", "unit": "$", "scale": "millions", "period": "FY2023"},
    ])
    assert res.confidence == 1.0
    assert res.warnings == []


def test_scale_word_in_unit_is_normalized_not_flagged():
    res = validate_numeric_facts([{"name": "ebitda", "value": 50, "unit": "millions", "period": "FY2022"}])
    assert res.warnings == []  # "millions" relocated to scale, no warn
    fct = NumericFact.from_dict({"name": "x", "value": 1, "unit": "millions"})
    assert fct.scale == "million" and fct.unit == ""


def test_non_numeric_value_warns():
    res = validate_numeric_facts([{"name": "revenue", "value": "about a lot", "period": "FY2023"}])
    assert any(f.code == "value_not_numeric" and f.severity == "warn" for f in res.findings)
    assert res.confidence < 1.0


def test_missing_period_is_info_not_warn():
    res = validate_numeric_facts([{"name": "revenue", "value": 100, "unit": "$"}])
    assert any(f.code == "missing_period" and f.severity == "info" for f in res.findings)
    assert res.warnings == []  # relaxed → confidence stays 1.0
    assert res.confidence == 1.0


def test_negative_revenue_warns():
    res = validate_numeric_facts([{"name": "total revenue", "value": -5, "period": "FY2023"}])
    assert any(f.code == "implausible_sign" and f.severity == "warn" for f in res.findings)


def test_reconcile_pass_and_fail():
    p1 = NumericFact.from_dict({"name": "segment A revenue", "value": 3})
    p2 = NumericFact.from_dict({"name": "segment B revenue", "value": 4})
    ok_total = NumericFact.from_dict({"name": "total revenue", "value": 7})
    bad_total = NumericFact.from_dict({"name": "total revenue", "value": 10})
    assert reconcile([p1, p2], ok_total) is None
    f = reconcile([p1, p2], bad_total)
    assert f is not None and f.code == "reconciliation" and f.severity == "warn"


def test_reconciliation_group_detected_end_to_end():
    res = validate_numeric_facts([
        {"name": "segment A revenue", "value": 3},
        {"name": "segment B revenue", "value": 4},
        {"name": "total revenue", "value": 10},  # 3+4 != 10
    ])
    assert any(f.code == "reconciliation" for f in res.findings)
