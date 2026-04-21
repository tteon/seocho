#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
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


def _load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        for summary in payload.get("summaries", []):
            row = {
                "artifact": path.name,
                "scenario": payload.get("scenario", ""),
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
    columns = ("scenario", "mode", *METRIC_COLUMNS, "artifact")
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
    parser.add_argument("--json", action="store_true", help="Emit machine-readable rows.")
    args = parser.parse_args()

    rows = _load_rows(args.artifacts)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
