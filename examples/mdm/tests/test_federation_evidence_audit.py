from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_audit():
    path = Path(__file__).resolve().parents[1] / "19_federation_evidence_audit.py"
    spec = importlib.util.spec_from_file_location("federation_evidence_audit", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_dashboard_compares_category_federation_to_provider_baseline() -> None:
    audit = _load_audit()
    dashboard = audit._run_dashboard(
        aggregate={
            "n_cases": 2,
            "topology": "single_dbms_category_databases",
            "llm_selector_enabled": True,
            "lanes": {
                "category-federation": {
                    "n": 2,
                    "token_f1": 0.4,
                    "overlap": 0.5,
                    "abstain": 0.25,
                    "ctx_chars": 1000,
                }
            },
        },
        baseline_aggregate={
            "lanes": {
                "federation": {
                    "n": 2,
                    "token_f1": 0.3,
                    "overlap": 0.4,
                    "abstain": 0.5,
                    "ctx_chars": 2000,
                },
                "silo-a": {
                    "n": 2,
                    "token_f1": 0.35,
                    "overlap": 0.4,
                    "abstain": 0.5,
                    "ctx_chars": 500,
                },
            },
            "partial_failure_degradation": {"providers_2": 0.35},
        },
        scenario_registry=[],
        run_prefix="category-run",
        baseline_run_prefix="baseline-run",
    )

    assert dashboard["beats_provider_federation"] is True
    assert dashboard["beats_best_silo"] is True
    assert dashboard["best_silo_name"] == "silo-a"
    assert dashboard["deltas_vs_provider_federation"]["token_f1"] == 0.1
    assert dashboard["deltas_vs_provider_federation"]["abstain"] == -0.25
    assert dashboard["deltas_vs_provider_federation"]["context_chars"] == -1000
    assert dashboard["category_federation"]["context_efficiency_per_1k_chars"] == 0.4


def test_scenario_registry_summarizes_prompt_ontology_gate_results() -> None:
    audit = _load_audit()
    registry = audit._summarize_scenario_registry(
        {
            "scenarios": [
                {
                    "scenario_id": "prompt-a__ontology-a",
                    "prompt": {"prompt_id": "prompt-a", "intent": "fact-first"},
                    "ontology": {"ontology_id": "ontology-a", "modules": ["be", "fbc"]},
                    "cases": [{"case_id": "c1"}, {"case_id": "c2"}],
                    "results": [
                        {
                            "provider_id": "deepseek",
                            "nodes_created": 2,
                            "rels_created": 3,
                            "latency_s": 1.0,
                            "error": "",
                        },
                        {
                            "provider_id": "minimax27",
                            "nodes_created": 4,
                            "rels_created": 5,
                            "latency_s": 3.0,
                            "error": "timeout",
                        },
                    ],
                    "candidate_census": {
                        "entities": 10,
                        "facts": 7,
                        "cross_provider_clusters": 3,
                        "duplicate_ratio": 0.2,
                        "generic_entity_ratio": 0.1,
                    },
                    "baseline_census": {"facts": 4},
                    "gate": {
                        "promote_to_full_reindex": True,
                        "fact_gain": 3,
                        "cross_provider_cluster_gain": 2,
                        "rule": "facts improve",
                    },
                }
            ]
        }
    )

    assert registry == [
        {
            "scenario_id": "prompt-a__ontology-a",
            "prompt_id": "prompt-a",
            "prompt_intent": "fact-first",
            "ontology_id": "ontology-a",
            "ontology_modules": ["be", "fbc"],
            "case_count": 2,
            "provider_count": 2,
            "result_count": 2,
            "error_count": 1,
            "nodes_created": 6,
            "relationships_created": 8,
            "avg_latency_s": 2.0,
            "p90_latency_s": 3.0,
            "facts": 7,
            "baseline_facts": 4,
            "fact_gain": 3,
            "entities": 10,
            "cross_provider_clusters": 3,
            "cross_provider_cluster_gain": 2,
            "duplicate_ratio": 0.2,
            "generic_entity_ratio": 0.1,
            "promotion_verdict": "promote",
            "promotion_rule": "facts improve",
        }
    ]


def test_survivorship_policy_summary_counts_consensus_and_conflicts() -> None:
    audit = _load_audit()
    summary = audit._survivorship_policy_summary(
        [
            {"provider_count": 3, "distinct_value_count": 1},
            {"provider_count": 2, "distinct_value_count": 2},
            {"provider_count": 1, "distinct_value_count": 1},
        ]
    )

    assert summary["reviewed_fact_clusters"] == 3
    assert summary["multi_provider_fact_clusters"] == 2
    assert summary["consensus_clusters"] == 2
    assert summary["conflicting_value_clusters"] == 1
    assert "provider_id" in summary["recommended_policy"][3]
