from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "benchmarks" / "score_finder_artifacts.py"
)
SPEC = importlib.util.spec_from_file_location("score_finder_artifacts_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_score_summary_marks_answer_quality_as_not_ready(tmp_path):
    artifact = tmp_path / "cognee.json"
    summary = {
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

    row = MODULE._score_summary(artifact, "beginner", summary)

    assert row["overall"] == "not_ready"
    assert row["score"]["answer_quality"] == "bad"
    assert row["score"]["graph_projection"] == "watch"
    assert "answer_quality_gap" in row["gaps"]


def test_score_artifacts_flattens_all_summaries(tmp_path):
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
                        "add_latency_p50_ms": 20347.61,
                        "ask_latency_p50_ms": 1462.12,
                        "avg_nodes_created": 7.2,
                        "avg_relationships_created": 6.2,
                        "failure_count": 0,
                    }
                ],
            }
        )
    )

    rows = MODULE._score_artifacts([artifact])

    assert rows[0]["scenario"] == "advanced"
    assert rows[0]["overall"] == "needs_work"
    assert rows[0]["score"]["indexing_latency"] == "bad"
