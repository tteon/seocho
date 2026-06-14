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


# --- source-grounded numeric check (ADR-0131) ---
from seocho.numeric_validation import extract_source_numbers, ground_facts


def test_extract_source_numbers_handles_formats():
    nums = extract_source_numbers("Revenue was $1,234.5 million, up from (200) in 2021; margin 12.5%.")
    assert 1234.5 in nums
    assert 1234500000.0 in nums          # scale-expanded ($1,234.5 million)
    assert -200.0 in nums                # parenthesised negative
    assert 12.5 in nums


def test_grounded_value_passes_ungrounded_warns():
    src = "Total revenue was 539.2 million in FY2023; cost of sales 111.5 million."
    grounded = ground_facts([{"name": "revenue", "value": 539.2, "scale": "million"}], src)
    assert grounded.any_ungrounded is False and grounded.grounded == 1
    # a fabricated/wrong number not in the source → ungrounded warn
    bad = ground_facts([{"name": "revenue", "value": 612.7, "scale": "million"}], src)
    assert bad.any_ungrounded is True


def test_scale_aware_grounding():
    src = "Net income of $539.2 million."
    # extractor reported absolute value 539200000 with scale million — still grounded
    res = ground_facts([{"name": "net income", "value": 539200000, "scale": ""}], src)
    assert res.grounded == 1


def test_validate_with_source_text_folds_grounding():
    src = "Revenue 100 in FY2023."
    res = validate_numeric_facts([{"name": "revenue", "value": 999, "period": "FY2023"}], source_text=src)
    assert any(f.code == "ungrounded_value" for f in res.warnings)
    assert res.confidence < 1.0
    # without source_text, no grounding warn (precision preserved)
    clean = validate_numeric_facts([{"name": "revenue", "value": 999, "period": "FY2023"}])
    assert not any(f.code == "ungrounded_value" for f in clean.findings)
