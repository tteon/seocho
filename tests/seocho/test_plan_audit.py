from seocho.eval.plan_audit import audit_profile, compare_plans


def test_profile_audit_attributes_scan_expand_and_cardinality_error() -> None:
    plan = {
        "operatorType": "ProduceResults",
        "args": {"Rows": 1, "EstimatedRows": 1, "DbHits": 0},
        "children": [
            {
                "operatorType": "EagerAggregation",
                "args": {"Rows": 1, "EstimatedRows": 1, "DbHits": 2},
                "children": [
                    {
                        "operatorType": "VarLengthExpand(All)",
                        "args": {"Rows": 500, "EstimatedRows": 5, "DbHits": 900},
                        "children": [
                            {
                                "operatorType": "AllNodesScan",
                                "args": {"Rows": 1000, "EstimatedRows": 1000, "DbHits": 1001},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    audit = audit_profile(plan)
    assert audit.findings == (
        "cardinality_misestimation",
        "eager_aggregation",
        "global_node_scan",
        "variable_length_expansion",
    )
    assert audit.total_db_hits == 1903
    assert len(audit.fingerprint) == 16


def test_plan_comparison_requires_semantic_parity_before_promotion() -> None:
    audit = audit_profile(
        {"operatorType": "NodeUniqueIndexSeek", "args": {"Rows": 1, "DbHits": 2}, "children": []}
    )
    faster_but_wrong = compare_plans(
        audit,
        audit,
        baseline_p95_ms=60,
        candidate_p95_ms=2,
        baseline_result_hashes=("expected",),
        candidate_result_hashes=("different",),
    )
    assert faster_but_wrong.speedup == 30
    assert faster_but_wrong.semantic_parity is False
    assert faster_but_wrong.promotable is False


def test_plan_comparison_reports_db_hit_reduction() -> None:
    baseline = audit_profile(
        {"operatorType": "AllNodesScan", "args": {"Rows": 100, "DbHits": 100}, "children": []}
    )
    candidate = audit_profile(
        {"operatorType": "NodeUniqueIndexSeek", "args": {"Rows": 1, "DbHits": 5}, "children": []}
    )
    comparison = compare_plans(
        baseline,
        candidate,
        baseline_p95_ms=50,
        candidate_p95_ms=5,
        baseline_result_hashes=("same",),
        candidate_result_hashes=("same",),
    )
    assert comparison.db_hits_reduction == 0.95
    assert comparison.promotable is True
