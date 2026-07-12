#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from seocho.eval.customer_query_dataset import classify_customer_query


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line]
    outcomes = []
    for row in rows:
        routed = classify_customer_query(row["question"])
        outcomes.append(routed is not None and routed.intent == row["gold"]["intent"])
    report = {
        "schema_version": "seocho.customer-query-routing.v1",
        "queries": len(rows),
        "accuracy": sum(outcomes) / len(outcomes),
        "errors": len(outcomes) - sum(outcomes),
        "intent_counts": dict(sorted(Counter(row["gold"]["intent"] for row in rows).items())),
        "passed": all(outcomes),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
