"""Unit tests for sec_temporal_run.py scoring — no network, no LLM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_run as run


def test_extract_usd_scale_aware():
    vals = run.extract_usd_values("revenue was $416,161 million")
    assert any(abs(v - 416_161_000_000) < 1 for v in vals)
    vals = run.extract_usd_values("about $416.2 billion this year")
    assert any(abs(v - 416_200_000_000) < 1 for v in vals)
    vals = run.extract_usd_values("416161000000 dollars")
    assert any(abs(v - 416_161_000_000) < 1 for v in vals)


def test_value_matches_handles_million_and_billion_forms():
    gold = 416_161_000_000
    assert run.value_matches("$416,161 million", gold)       # verbose grounded
    assert run.value_matches("approximately $416.2 billion", gold)  # rounded prior
    assert run.value_matches("416,161", gold) is False       # bare millions w/o scale != raw
    assert run.value_matches("$391,035 million", gold) is False  # different year


def test_value_matches_rejects_zero_and_nonnumeric():
    assert run.value_matches("$100 million", 0) is False
    assert run.value_matches("I don't know", 391_035_000_000) is False


def test_temporal_verdict_correct_wrong_no_match():
    asked = 416_161_000_000          # FY2025
    other = [391_035_000_000, 383_285_000_000]  # FY2024, FY2023 in same corpus
    assert run.temporal_verdict("$416,161 million", asked, other) == "correct"
    assert run.temporal_verdict("$391,035 million", asked, other) == "wrong_year"
    assert run.temporal_verdict("no data found", asked, other) == "no_match"
    # answer stating both years still resolves the asked one
    assert run.temporal_verdict("FY2024 was $391,035M and FY2025 was $416,161M",
                                asked, other) == "correct"


def test_aggregate_three_abs():
    records = [
        # fresh: prior knows it; grounded also right
        {"prior_stale": False, "closed_book_match": True, "grounded_match": True,
         "temporal": "correct"},
        # stale: prior wrong, grounded right -> the money case
        {"prior_stale": True, "closed_book_match": False, "grounded_match": True,
         "temporal": "correct"},
        # stale: both wrong, grounded retrieved wrong year
        {"prior_stale": True, "closed_book_match": False, "grounded_match": False,
         "temporal": "wrong_year"},
    ]
    s = run.aggregate(records)
    assert s["n"] == 3
    assert s["closed_book_vs_grounded"]["closed_book_acc"] == round(1 / 3, 3)
    assert s["closed_book_vs_grounded"]["grounded_acc"] == round(2 / 3, 3)
    assert s["prior_staleness"]["stale_n"] == 2
    assert s["prior_staleness"]["stale_closed_book_acc"] == 0.0
    assert s["prior_staleness"]["stale_grounded_acc"] == 0.5
    assert s["temporal_resolution"]["grounded_n"] == 3
    assert s["temporal_resolution"]["correct"] == 2
    assert s["temporal_resolution"]["wrong_year"] == 1
    assert s["temporal_resolution"]["resolution_rate"] == round(2 / 3, 3)


def test_aggregate_empty():
    s = run.aggregate([])
    assert s["n"] == 0
    assert s["closed_book_vs_grounded"]["closed_book_acc"] is None
