"""Bounded-staleness freshness policy (seocho-ia4.6)."""

from __future__ import annotations

from seocho.ontology.freshness import (
    FreshnessSignals,
    evaluate_freshness,
    freshness_to_drift_policy,
)


def test_fresh_serves():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=False))
    assert d.decision == "serve" and d.staleness == 0.0 and not d.blocks


def test_irrelevant_drift_serves():
    # drift present but it doesn't touch this query's labels -> serve
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, version_distance=4,
                                            drift_relevance=0.0))
    assert d.decision == "serve" and d.reason == "drift_irrelevant_to_query"


def test_relevant_within_bound_repairs():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, version_distance=2,
                                            drift_relevance=1.0),
                           max_version_distance=2)
    assert d.decision == "repair" and not d.blocks


def test_relevant_beyond_bound_refuses():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, version_distance=3,
                                            drift_relevance=1.0),
                           max_version_distance=2)
    assert d.decision == "refuse" and d.blocks
    assert freshness_to_drift_policy(d) == "block"


def test_insufficient_coverage_refuses_blind():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, drift_relevance=1.0,
                                            stamp_coverage=0.1),
                           min_coverage=0.5)
    assert d.decision == "refuse" and d.reason == "insufficient_stamp_coverage"


def test_contract_too_old_refuses():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, version_distance=1,
                                            drift_relevance=1.0, version_age_days=200),
                           max_version_distance=5, max_age_days=90)
    assert d.decision == "refuse" and d.reason == "contract_too_old"


def test_staleness_is_distance_times_relevance():
    d = evaluate_freshness(FreshnessSignals(version_mismatch=True, version_distance=4,
                                            drift_relevance=0.5),
                           max_version_distance=10)
    assert d.staleness == 2.0
