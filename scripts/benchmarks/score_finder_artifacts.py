#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _classify_high(value: float, *, good: float, watch: float) -> str:
    if value >= good:
        return "good"
    if value >= watch:
        return "watch"
    return "bad"


def _classify_low(value: float, *, good: float, watch: float) -> str:
    if value <= good:
        return "good"
    if value <= watch:
        return "watch"
    return "bad"


def _classify_graph_projection(nodes: float, relationships: float) -> str:
    if nodes >= 5.0 and relationships >= 4.0:
        return "good"
    if nodes >= 1.0 and relationships >= 1.0:
        return "watch"
    return "bad"


def _gaps(score: dict[str, str]) -> list[str]:
    gaps: list[str] = []
    if score["answer_quality"] != "good":
        gaps.append("answer_quality_gap")
    if score["indexing_latency"] != "good":
        gaps.append("indexing_latency_gap")
    if score["query_latency"] != "good":
        gaps.append("query_latency_gap")
    if score["graph_projection"] != "good":
        gaps.append("graph_projection_gap")
    if score["reliability"] != "good":
        gaps.append("reliability_gap")
    return gaps


def _overall(score: dict[str, str]) -> str:
    if score["answer_quality"] == "bad" or score["reliability"] == "bad":
        return "not_ready"
    if "bad" in score.values():
        return "needs_work"
    if "watch" in score.values():
        return "usable_with_gaps"
    return "good"


def _score_summary(artifact: Path, scenario: str, summary: dict[str, Any]) -> dict[str, Any]:
    score = {
        "answer_quality": _classify_high(
            float(summary.get("contains_match_rate") or 0.0),
            good=0.9,
            watch=0.75,
        ),
        "indexing_latency": _classify_low(
            float(summary.get("add_latency_p50_ms") or 0.0),
            good=10_000.0,
            watch=20_000.0,
        ),
        "query_latency": _classify_low(
            float(summary.get("ask_latency_p50_ms") or 0.0),
            good=1_500.0,
            watch=3_000.0,
        ),
        "graph_projection": _classify_graph_projection(
            float(summary.get("avg_nodes_created") or 0.0),
            float(summary.get("avg_relationships_created") or 0.0),
        ),
        "reliability": "good" if int(summary.get("failure_count") or 0) == 0 else "bad",
    }
    return {
        "artifact": artifact.name,
        "scenario": scenario,
        "mode": summary.get("mode", ""),
        "overall": _overall(score),
        "score": score,
        "gaps": _gaps(score),
        "metrics": {
            "record_count": summary.get("record_count"),
            "contains_match_rate": summary.get("contains_match_rate"),
            "exact_match_rate": summary.get("exact_match_rate"),
            "add_latency_p50_ms": summary.get("add_latency_p50_ms"),
            "ask_latency_p50_ms": summary.get("ask_latency_p50_ms"),
            "avg_nodes_created": summary.get("avg_nodes_created"),
            "avg_relationships_created": summary.get("avg_relationships_created"),
            "failure_count": summary.get("failure_count"),
        },
    }


def _score_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        scenario = str(payload.get("scenario", ""))
        for summary in payload.get("summaries", []):
            rows.append(_score_summary(path, scenario, summary))
    return rows


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        return ",".join(value)
    if value is None:
        return ""
    return str(value)


def _render_table(rows: list[dict[str, Any]]) -> str:
    columns = (
        "scenario",
        "mode",
        "overall",
        "answer_quality",
        "indexing_latency",
        "query_latency",
        "graph_projection",
        "reliability",
        "gaps",
        "artifact",
    )
    table: list[list[str]] = []
    for row in rows:
        score = row["score"]
        table.append(
            [
                _format_value(row.get("scenario")),
                _format_value(row.get("mode")),
                _format_value(row.get("overall")),
                _format_value(score.get("answer_quality")),
                _format_value(score.get("indexing_latency")),
                _format_value(score.get("query_latency")),
                _format_value(score.get("graph_projection")),
                _format_value(score.get("reliability")),
                _format_value(row.get("gaps")),
                _format_value(row.get("artifact")),
            ]
        )
    widths = [
        max(len(column), *(len(row[index]) for row in table)) if table else len(column)
        for index, column in enumerate(columns)
    ]
    header = " | ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
    sep = " | ".join("-" * width for width in widths)
    body = [
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in table
    ]
    return "\n".join([header, sep, *body])


def main() -> int:
    parser = argparse.ArgumentParser(description="Score FinDER benchmark artifacts.")
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable score rows.")
    args = parser.parse_args()

    rows = _score_artifacts(args.artifacts)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
