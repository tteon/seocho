"""Metric-threshold profile gate for text2cypher (seocho-ia4)."""
from __future__ import annotations
from seocho.query.profile_gate import (
    PlanMetrics, ProfileThresholds, evaluate_plan, parse_explain_metrics,
)


def test_clean_plan_no_breach():
    d = evaluate_plan(PlanMetrics(db_hits=50, estimated_rows=10, elapsed_ms=20,
                                  operators=["NodeIndexSeek", "Expand(Into)"],
                                  rows_returned=5, used_index=True))
    assert not d.breached and d.improve_directive == ""


def test_full_scan_breach_directs_to_index():
    d = evaluate_plan(PlanMetrics(operators=["AllNodesScan", "Filter"], db_hits=10,
                                  used_index=False))
    assert d.breached and any("full_scan" in r for r in d.reasons)
    assert "INDEXED" in d.improve_directive


def test_cartesian_breach():
    d = evaluate_plan(PlanMetrics(operators=["CartesianProduct"], db_hits=10))
    assert d.breached and "cartesian_product" in d.reasons
    assert "cartesian product" in d.improve_directive.lower()


def test_db_hits_and_slo_breach():
    d = evaluate_plan(PlanMetrics(db_hits=5_000_000, elapsed_ms=8000,
                                  operators=["NodeIndexSeek"], used_index=True),
                      ProfileThresholds(max_db_hits=200_000, slo_ms=1000))
    assert d.breached
    assert any("db_hits" in r for r in d.reasons)
    assert any("elapsed_ms" in r for r in d.reasons)


def test_rows_and_estimated_breach():
    d = evaluate_plan(PlanMetrics(estimated_rows=5e6, rows_returned=9999,
                                  operators=["NodeIndexSeek"], used_index=True))
    assert d.breached
    assert any("estimated_rows" in r for r in d.reasons)
    assert any("rows_returned" in r for r in d.reasons)


def test_used_index_suppresses_full_scan_flag():
    # NodeByLabelScan but an index was used elsewhere -> not flagged as full scan
    d = evaluate_plan(PlanMetrics(operators=["NodeByLabelScan"], db_hits=10, used_index=True))
    assert not any("full_scan" in r for r in d.reasons)


def test_parse_explain_metrics_walks_plan_tree():
    plan = {"operatorType": "ProduceResults", "children": [
        {"operatorType": "Filter", "arguments": {"DbHits": 100, "EstimatedRows": 5},
         "children": [
            {"operatorType": "NodeIndexSeek", "arguments": {"DbHits": 2, "EstimatedRows": 1}}]}]}
    m = parse_explain_metrics(plan, elapsed_ms=12.0)
    assert m.db_hits == 102 and m.estimated_rows == 5 and m.used_index is True
    assert "NodeIndexSeek" in m.operators and m.elapsed_ms == 12.0
