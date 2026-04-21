from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "benchmarks" / "compare_finder_artifacts.py"
)
SPEC = importlib.util.spec_from_file_location("compare_finder_artifacts_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_load_rows_flattens_artifact_summaries(tmp_path):
    artifact = tmp_path / "finder.json"
    artifact.write_text(
        json.dumps(
            {
                "scenario": "advanced",
                "summaries": [
                    {
                        "mode": "local",
                        "record_count": 5,
                        "contains_match_rate": 1.0,
                        "exact_match_rate": 0.0,
                        "add_latency_p50_ms": 123.0,
                        "ask_latency_p50_ms": 45.0,
                        "avg_nodes_created": 6.0,
                        "avg_relationships_created": 4.0,
                        "failure_count": 0,
                    }
                ],
            }
        )
    )

    rows = MODULE._load_rows([artifact])

    assert rows == [
        {
            "artifact": "finder.json",
            "scenario": "advanced",
            "category": "",
            "mode": "local",
            "record_count": 5,
            "contains_match_rate": 1.0,
            "exact_match_rate": 0.0,
            "add_latency_p50_ms": 123.0,
            "ask_latency_p50_ms": 45.0,
            "avg_nodes_created": 6.0,
            "avg_relationships_created": 4.0,
            "failure_count": 0,
        }
    ]


def test_render_table_contains_core_metrics():
    table = MODULE._render_table(
        [
            {
                "artifact": "a.json",
                "scenario": "beginner",
                "mode": "cognee-local-recall",
                "record_count": 5,
                "contains_match_rate": 0.6,
                "exact_match_rate": 0.0,
                "add_latency_p50_ms": 9866.21,
                "ask_latency_p50_ms": 1129.64,
                "avg_nodes_created": 5.4,
                "avg_relationships_created": 3.8,
                "failure_count": 0,
            }
        ]
    )

    assert "cognee-local-recall" in table
    assert "contains_match_rate" in table


def test_load_rows_can_group_by_category(tmp_path):
    artifact = tmp_path / "finder.json"
    artifact.write_text(
        json.dumps(
            {
                "scenario": "all",
                "summaries": [
                    {
                        "mode": "local",
                        "records": [
                            {
                                "category": "Financials",
                                "add_latency_ms": 10.0,
                                "ask_latency_ms": 1.0,
                                "contains_match": True,
                                "exact_match": False,
                                "nodes_created": 6,
                                "relationships_created": 5,
                                "error": "",
                            },
                            {
                                "category": "Financials",
                                "add_latency_ms": 30.0,
                                "ask_latency_ms": 3.0,
                                "contains_match": False,
                                "exact_match": False,
                                "nodes_created": 2,
                                "relationships_created": 1,
                                "error": "miss",
                            },
                        ],
                    }
                ],
            }
        )
    )

    rows = MODULE._load_rows([artifact], group_by="category")

    assert rows[0]["category"] == "Financials"
    assert rows[0]["record_count"] == 2
    assert rows[0]["contains_match_rate"] == 0.5
    assert rows[0]["add_latency_p50_ms"] == 20.0
    assert rows[0]["failure_count"] == 1
