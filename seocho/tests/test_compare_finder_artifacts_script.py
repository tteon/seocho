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
