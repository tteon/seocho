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

# --- read-time repair (the reconciliation that makes "repair" real) ----------

from seocho.ontology.freshness import (  # noqa: E402
    plan_read_repair,
    repair_read,
)


def test_repair_read_drops_soft_deleted_records():
    records = [
        {"n": {"name": "Acme", "revenue": 100}},
        {"n": {"name": "OldCo", "_ontology_soft_deleted_at": "2.0.0"}},  # logically removed
        {"n": {"name": "Globex", "revenue": 200}},
    ]
    kept, report = repair_read(records)
    names = [r["n"]["name"] for r in kept]
    assert names == ["Acme", "Globex"], "soft-deleted rows must not be served"
    assert report.dropped_records == 1 and report.reconcilable


def test_repair_read_strips_deprecated_properties():
    records = [{"n": {"name": "Acme", "legacy_code": "X"}}]
    kept, report = repair_read(records, deprecated_properties=frozenset({"legacy_code"}))
    assert "legacy_code" not in kept[0]["n"] and kept[0]["n"]["name"] == "Acme"
    assert report.stripped_property_keys == 1


def test_repair_read_passes_through_when_no_drift_artifacts():
    records = [{"n": {"name": "Acme"}}, {"m": {"name": "Bob"}}]
    kept, report = repair_read(records)
    assert kept == records and report.dropped_records == 0


def test_repair_read_refuses_when_not_reconcilable():
    records = [{"n": {"name": "Acme"}}]
    kept, report = repair_read(records, reconcilable=False)
    assert kept == records and report.reconcilable is False


def test_plan_read_repair_from_migration_plan():
    mplan = {
        "removals": [
            {"type": "property", "label": "Company", "property": "legacy_code"},
            {"type": "node", "label": "OldThing"},
        ],
        "cypher_statements": [{"data_loss": False}],   # soft-delete -> reconcilable
    }
    plan = plan_read_repair(mplan)
    assert plan.deprecated_properties == frozenset({"legacy_code"})
    assert plan.removed_labels == frozenset({"OldThing"})
    assert plan.reconcilable is True


def test_plan_read_repair_marks_destructive_change_unreconcilable():
    mplan = {"removals": [], "cypher_statements": [{"data_loss": True}]}  # hard delete
    plan = plan_read_repair(mplan)
    assert plan.reconcilable is False and plan.reason
