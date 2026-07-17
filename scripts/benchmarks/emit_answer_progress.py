#!/usr/bin/env python3
"""Export durable long-running answer progress from a JSONL checkpoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from seocho.metrics import enable_metrics, shutdown_metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    args = parser.parse_args()
    rows = []
    if args.checkpoint.exists():
        with open(args.checkpoint, 'r') as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    latest = {row["query_id"]: row for row in rows if row.get("query_id")}
    completed = len(latest)
    failures = sum(
        not (row.get("status_ok") and row.get("missing_ok")) or row.get("leakage")
        for row in latest.values()
    )
    os.environ["OTEL_SERVICE_NAME"] = "seocho-evaluation-progress"
    os.environ["OTEL_SERVICE_INSTANCE_ID"] = args.cohort
    metrics = enable_metrics(backend="otlp", endpoint=args.otlp_grpc)
    labels = {"cohort": args.cohort}
    metrics.set("seocho.evaluation.answer.progress", completed / args.total, labels)
    metrics.set("seocho.evaluation.answer.completed", completed, labels)
    metrics.set("seocho.evaluation.answer.failures", failures, labels)
    shutdown_metrics()
    print(
        json.dumps({"completed": completed, "total": args.total, "failures": failures})
    )


if __name__ == "__main__":
    main()
