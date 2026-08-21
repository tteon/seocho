"""Regression tests for RDF-governance semantic utility measurement."""

from seocho.eval.semantic_scorecard import compare_semantic_utility, score_semantic_utility


def _indexing() -> dict:
    return {
        "files_found": 10,
        "files_indexed": 10,
        "files_failed": 0,
        "total_nodes": 20,
        "total_relationships": 10,
        "validation_errors_count": 1,
    }


def test_scorecard_separates_observable_proxies_from_gold_quality() -> None:
    scorecard = score_semantic_utility(
        _indexing(),
        [
            {"answer": "Jane leads Acme", "expect": "Jane", "coverage": 1.0,
             "support_status": "supported", "selected_triple_count": 2, "latency_s": 0.5},
            {"answer": "", "empty": True, "coverage": 0.5, "missing_slots": ["period"], "latency_s": 1.0},
        ],
        governance={"promotable": True, "bundle_sha256": "a" * 64},
    ).to_dict()
    assert scorecard["indexing"]["admission_rate"] == 1.0
    assert scorecard["indexing"]["relation_density"] == 0.5
    assert scorecard["agent"]["mean_evidence_coverage"] == 0.75
    assert scorecard["agent"]["reference_contains_rate"] == 1.0
    assert scorecard["governance"]["promotable"] is True
    assert "relation_density is not relation recall" in " ".join(scorecard["limitations"])


def test_lift_comparison_requires_sample_and_quality_gates() -> None:
    baseline = score_semantic_utility(_indexing(), [
        {"answer": "Jane", "expect": "Jane", "coverage": 0.6, "support_status": "supported", "missing_slots": ["period"]}
        for _ in range(10)
    ]).to_dict()
    governed = score_semantic_utility(_indexing(), [
        {"answer": "Jane", "expect": "Jane", "coverage": 0.7, "support_status": "supported", "missing_slots": []}
        for _ in range(10)
    ]).to_dict()
    comparison = compare_semantic_utility(baseline, governed)
    assert comparison["verdict"] == "supports_hypothesis"
    assert comparison["deltas"]["mean_evidence_coverage"] == 0.1

    too_small = compare_semantic_utility(baseline, governed, minimum_questions=11)
    assert too_small["verdict"] == "insufficient_sample"
