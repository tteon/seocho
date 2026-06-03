"""Unit tests for sec_temporal_bench.py — no network, no LLM.

Exercises the pure conversion logic against synthetic companyfacts-shaped
fixtures: annual-frame selection/dedup, fiscal-year labelling, value
formatting, concept-group fallback, and prior-stale tagging.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench


def _units(records):
    return {"USD": records}


def test_fiscal_year_from_frame():
    assert bench.fiscal_year_from_frame("CY2024") == 2024
    # quarterly / instant / missing frames are not annual gold
    assert bench.fiscal_year_from_frame("CY2024Q3") is None
    assert bench.fiscal_year_from_frame("CY2024Q4I") is None
    assert bench.fiscal_year_from_frame(None) is None
    assert bench.fiscal_year_from_frame("") is None


def test_select_annual_facts_dedups_and_orders_newest_first():
    units = _units([
        {"frame": "CY2023", "form": "10-K", "end": "2023-09-30", "val": 383285000000},
        {"frame": "CY2024", "form": "10-K", "end": "2024-09-28", "val": 391035000000},
        {"frame": "CY2025", "form": "10-K", "end": "2025-09-27", "val": 416161000000},
        # comparative copy of CY2023 from a later filing — must be deduped out
        {"frame": "CY2023", "form": "10-K", "end": "2023-09-30", "val": 383285000000},
        # a quarterly frame — must be ignored
        {"frame": "CY2025Q2", "form": "10-Q", "end": "2025-03-31", "val": 100000000000},
    ])
    facts = bench.select_annual_facts(units, n_years=3)
    assert [f["fiscal_year"] for f in facts] == [2025, 2024, 2023]
    assert facts[0]["value"] == 416161000000


def test_select_annual_facts_respects_n_years():
    units = _units([
        {"frame": f"CY{y}", "form": "10-K", "end": f"{y}-12-31", "val": y}
        for y in (2021, 2022, 2023, 2024, 2025)
    ])
    facts = bench.select_annual_facts(units, n_years=2)
    assert [f["fiscal_year"] for f in facts] == [2025, 2024]


def test_select_annual_facts_skips_non_10k_frames():
    units = _units([
        {"frame": "CY2024", "form": "10-Q", "end": "2024-09-28", "val": 1},
    ])
    assert bench.select_annual_facts(units, n_years=3) == []


def test_format_value_usd_millions():
    assert bench.format_value(391035000000, "USD") == "$391,035 million"
    assert bench.format_value(416161000000, "USD") == "$416,161 million"


def test_pick_concept_falls_back_across_group():
    usgaap = {
        # primary concept absent; fallback "Revenues" present
        "Revenues": {"units": _units([
            {"frame": "CY2024", "form": "10-K", "end": "2024-12-31", "val": 5},
        ])},
    }
    group = next(g for g in bench.CONCEPT_GROUPS if g["metric"] == "revenue")
    picked = bench.pick_concept(usgaap, group, n_years=3)
    assert picked is not None
    assert picked["concept"] == "Revenues"


def test_pick_concept_merges_recent_years_across_migrated_tags():
    # old tag carries stale years, new tag carries recent ones — recent must win
    usgaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": _units([
            {"frame": "CY2024", "form": "10-K", "end": "2024-12-31", "val": 24},
            {"frame": "CY2025", "form": "10-K", "end": "2025-12-31", "val": 25},
        ])},
        "Revenues": {"units": _units([
            {"frame": "CY2019", "form": "10-K", "end": "2019-12-31", "val": 19},
            {"frame": "CY2020", "form": "10-K", "end": "2020-12-31", "val": 20},
        ])},
    }
    group = next(g for g in bench.CONCEPT_GROUPS if g["metric"] == "revenue")
    picked = bench.pick_concept(usgaap, group, n_years=3)
    years = [f["fiscal_year"] for f in picked["facts"]]
    assert years == [2025, 2024, 2020]  # newest 3 across both tags, no stale-only result


def test_build_qa_rows_tags_prior_stale_and_shapes_corpus():
    usgaap = {
        "Revenues": {"units": _units([
            {"frame": "CY2023", "form": "10-K", "end": "2023-12-31", "val": 100000000000},
            {"frame": "CY2025", "form": "10-K", "end": "2025-12-31", "val": 120000000000},
        ])},
    }
    rows = bench.build_qa_rows(
        "Acme Corp", "ACME", usgaap, n_years=3, cutoff_year=2024
    )
    assert len(rows) == 2
    by_year = {r["fiscal_year"]: r for r in rows}
    # FY2023 <= cutoff -> prior known; FY2025 > cutoff -> prior stale
    assert by_year[2023]["prior_stale"] is False
    assert by_year[2025]["prior_stale"] is True
    # the corpus carries BOTH years so retrieval must disambiguate the year
    assert len(by_year[2025]["corpus"]) == 2
    assert any("fiscal year 2023" in c for c in by_year[2025]["corpus"])
    assert "fiscal year 2025" in by_year[2025]["question"]
    assert by_year[2025]["answer"] == "$120,000 million"
    assert by_year[2025]["gold_entities"] == ["Acme Corp", "revenue"]


def test_build_qa_rows_empty_when_no_concepts():
    assert bench.build_qa_rows("X", "X", {}, n_years=3, cutoff_year=2024) == []
