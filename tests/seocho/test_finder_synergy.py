"""Tests for the FinDER synergy benchmark cost arm (council seocho-9xo).

The deterministic half — signal→tier routing + routed-vs-all-frontier cost on the
real FinDER signal distribution — is unit-tested offline (no LLM). The live
support-parity arm is exercised separately via MARA.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from seocho.benchmarking import FinDERBenchmarkCase
from seocho.routing import ModelRouter, ModelTier

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "finder_synergy", ROOT / "scripts" / "benchmarks" / "finder_synergy.py"
)
finder_synergy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finder_synergy)
route_tier_for_case = finder_synergy.route_tier_for_case
synergy_cost_report = finder_synergy.synergy_cost_report


def _case(cid, category, reasoning=""):
    return FinDERBenchmarkCase(
        case_id=cid, text="x", question="q", expected_answer="a",
        category=category, reasoning_type=reasoning,
    )


def test_hard_reasoning_routes_to_frontier():
    assert route_tier_for_case(_case("1", "Financials", "Compositional")) == ModelTier.FRONTIER
    assert route_tier_for_case(_case("2", "Footnotes", "Subtraction")) == ModelTier.FRONTIER


def test_financials_routes_to_frontier_even_without_reasoning_type():
    assert route_tier_for_case(_case("3", "Financials")) == ModelTier.FRONTIER


def test_single_passage_lookup_routes_to_fast():
    assert route_tier_for_case(_case("4", "CompanyOverview")) == ModelTier.FAST


def test_qualitative_categories_route_to_balanced():
    for cat in ("Footnotes", "Accounting", "Legal", "Risk", "Governance"):
        assert route_tier_for_case(_case("5", cat)) == ModelTier.BALANCED


def test_unknown_category_defaults_balanced():
    assert route_tier_for_case(_case("6", "Whatever")) == ModelTier.BALANCED


def test_cost_report_computes_ratio_and_saving():
    router = ModelRouter.mara_default()
    cases = (
        [_case(f"f{i}", "CompanyOverview") for i in range(8)]      # FAST x8
        + [_case(f"b{i}", "Risk") for i in range(1)]               # BALANCED x1
        + [_case(f"x{i}", "Financials") for i in range(1)]         # FRONTIER x1
    )
    rep = synergy_cost_report(cases, router)
    assert rep["n"] == 10
    assert rep["tier_counts"] == {"FAST": 8, "BALANCED": 1, "FRONTIER": 1}
    # cost = 8*1 + 1*3 + 1*10 = 21 ; all-frontier = 10*10 = 100 -> 0.21x
    assert rep["routed_cost"] == 21.0
    assert rep["all_frontier_cost"] == 100.0
    assert rep["cost_ratio"] == pytest.approx(0.21)
    assert rep["meets_0_6x_target"] is True


def test_all_frontier_workload_has_no_saving():
    router = ModelRouter.mara_default()
    cases = [_case(f"x{i}", "Financials", "Numeric") for i in range(5)]  # all FRONTIER
    rep = synergy_cost_report(cases, router)
    assert rep["cost_ratio"] == pytest.approx(1.0)
    assert rep["cost_saving_pct"] == 0.0
    assert rep["meets_0_6x_target"] is False
