#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any


METRIC_COLUMNS = (
    "record_count",
    "contains_match_rate",
    "exact_match_rate",
    "add_latency_p50_ms",
    "ask_latency_p50_ms",
    "avg_nodes_created",
    "avg_relationships_created",
    "failure_count",
)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return round(sum(1 for value in values if value) / len(values), 4)


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(median(values)), 2)


def _category_rows(path: Path, payload: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in summary.get("records", []):
        category = str(record.get("category") or "Uncategorized")
        groups.setdefault(category, []).append(record)

    rows: list[dict[str, Any]] = []
    for category in sorted(groups):
        records = groups[category]
        rows.append(
            {
                "artifact": path.name,
                "scenario": payload.get("scenario", ""),
                "category": category,
                "mode": summary.get("mode", ""),
                "record_count": len(records),
                "contains_match_rate": _rate([bool(record.get("contains_match")) for record in records]),
                "exact_match_rate": _rate([bool(record.get("exact_match")) for record in records]),
                "add_latency_p50_ms": _p50(
                    [float(record.get("add_latency_ms") or 0.0) for record in records]
                ),
                "ask_latency_p50_ms": _p50(
                    [float(record.get("ask_latency_ms") or 0.0) for record in records]
                ),
                "avg_nodes_created": _mean(
                    [float(record.get("nodes_created") or 0.0) for record in records]
                ),
                "avg_relationships_created": _mean(
                    [float(record.get("relationships_created") or 0.0) for record in records]
                ),
                "failure_count": sum(1 for record in records if record.get("error")),
            }
        )
    return rows


def _load_rows(paths: list[Path], *, group_by: str = "summary") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        for summary in payload.get("summaries", []):
            if group_by == "category":
                rows.extend(_category_rows(path, payload, summary))
                continue
            row = {
                "artifact": path.name,
                "scenario": payload.get("scenario", ""),
                "category": "",
                "mode": summary.get("mode", ""),
            }
            for column in METRIC_COLUMNS:
                row[column] = summary.get(column)
            rows.append(row)
    return rows


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if value is None:
        return ""
    return str(value)


def _render_table(rows: list[dict[str, Any]]) -> str:
    include_category = any(row.get("category") for row in rows)
    columns = (
        ("scenario", "category", "mode", *METRIC_COLUMNS, "artifact")
        if include_category
        else ("scenario", "mode", *METRIC_COLUMNS, "artifact")
    )
    table = [[_format_value(row.get(column)) for column in columns] for row in rows]
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
    parser = argparse.ArgumentParser(description="Compare FinDER benchmark JSON artifacts.")
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--group-by", choices=("summary", "category"), default="summary")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable rows.")
    args = parser.parse_args()

    rows = _load_rows(args.artifacts, group_by=args.group_by)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
