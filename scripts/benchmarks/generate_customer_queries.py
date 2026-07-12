#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.customer_query_dataset import generate_customer_queries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = list(generate_customer_queries(count=args.count, seed=args.seed))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    print(json.dumps({"rows": len(rows), "language": "en", "intents": len({row['gold']['intent'] for row in rows})}))


if __name__ == "__main__":
    main()
