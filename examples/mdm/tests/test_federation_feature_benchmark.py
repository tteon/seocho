from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_benchmark():
    path = Path(__file__).resolve().parents[1] / "20_federation_feature_benchmark.py"
    spec = importlib.util.spec_from_file_location("federation_feature_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_comparison_marks_category_quality_context_and_abstain_gains() -> None:
    benchmark = _load_benchmark()
    comparison = benchmark._comparison(
        profile="category-profile",
        category_lane={
            "n": 2,
            "token_f1": 0.4,
            "overlap": 0.5,
            "abstain": 0.2,
            "ctx_chars": 1000,
            "context_efficiency_per_1k_chars": 0.4,
        },
        provider_lane={
            "n": 2,
            "token_f1": 0.3,
            "overlap": 0.4,
            "abstain": 0.5,
            "ctx_chars": 2000,
            "context_efficiency_per_1k_chars": 0.15,
        },
        best_silo_name="silo-a",
        best_silo_lane={
            "n": 2,
            "token_f1": 0.35,
            "overlap": 0.4,
            "abstain": 0.4,
            "ctx_chars": 500,
            "context_efficiency_per_1k_chars": 0.7,
        },
    )

    assert comparison["beats_provider_federation"] is True
    assert comparison["beats_best_silo"] is True
    assert comparison["abstain_no_worse_than_provider"] is True
    assert comparison["uses_less_context_than_provider"] is True
    assert comparison["context_efficiency_improved"] is True
    assert comparison["deltas_vs_provider_federation"] == {
        "token_f1": 0.1,
        "abstain": -0.3,
        "context_chars": -1000,
        "context_efficiency_per_1k_chars": 0.25,
    }


def test_contract_gates_validate_audit_feature_outputs() -> None:
    benchmark = _load_benchmark()
    gates = benchmark._contract_gates(
        {
            "run_dashboard": {
                "category_federation": {"token_f1": 0.4},
                "provider_db_federation": {"token_f1": 0.3},
                "best_silo": {"token_f1": 0.35},
            },
            "routing_audit": [
                {"case_id": "c1", "abstain_reason": "answered"},
                {"case_id": "c2", "abstain_reason": "missing_metric"},
            ],
            "scenario_registry": [
                {"scenario_id": "s1", "promotion_verdict": "promote"},
                {"scenario_id": "s2", "promotion_verdict": "hold"},
            ],
            "survivorship_policy_summary": {
                "reviewed_fact_clusters": 3,
                "conflicting_value_clusters": 1,
            },
        },
        expected_cases=2,
    )

    assert gates["dashboard_contract_complete"] is True
    assert gates["routing_audit_coverage"]["pass"] is True
    assert gates["abstain_taxonomy_contract"]["pass"] is True
    assert gates["scenario_registry_contract"] == {
        "scenario_count": 2,
        "promoted_count": 1,
        "pass": True,
    }
    assert gates["survivorship_summary_contract"]["pass"] is True


def test_build_payload_from_temp_artifacts(tmp_path: Path) -> None:
    benchmark = _load_benchmark()
    baseline = tmp_path / "baseline.json"
    category = tmp_path / "category.json"
    audit = tmp_path / "audit.json"
    baseline.write_text(
        json.dumps(
            {
                "lanes": {
                    "federation": {
                        "n": 1,
                        "token_f1": 0.2,
                        "overlap": 0.2,
                        "abstain": 0.5,
                        "ctx_chars": 2000,
                    },
                    "silo-a": {
                        "n": 1,
                        "token_f1": 0.25,
                        "overlap": 0.2,
                        "abstain": 0.5,
                        "ctx_chars": 500,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    category.write_text(
        json.dumps(
            {
                "lanes": {
                    "category-federation": {
                        "n": 1,
                        "token_f1": 0.3,
                        "overlap": 0.3,
                        "abstain": 0.4,
                        "ctx_chars": 1000,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    audit.write_text(
        json.dumps(
            {
                "run_dashboard": {
                    "category_federation": {"token_f1": 0.3},
                    "provider_db_federation": {"token_f1": 0.2},
                    "best_silo": {"token_f1": 0.25},
                },
                "routing_audit": [{"case_id": "c1", "abstain_reason": "answered"}],
                "scenario_registry": [{"scenario_id": "s1", "promotion_verdict": "promote"}],
                "survivorship_policy_summary": {
                    "reviewed_fact_clusters": 1,
                    "conflicting_value_clusters": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = benchmark.build_payload(
        baseline_path=baseline,
        category_paths=[category],
        audit_path=audit,
    )

    assert payload["overall_verdict"] == "pass"
    assert payload["performance_gates"]["best_profile"] == tmp_path.name
    assert payload["recommended_next_experiments"][0]["experiment"] == "context_budget_optimizer"
