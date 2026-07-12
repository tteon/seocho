#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.customer_query_dataset import (
    generate_customer_queries,
    generate_customer_query_challenges,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--challenge-count", type=int, default=300)
    parser.add_argument("--challenge-output", type=Path)
    args = parser.parse_args()
    rows = list(generate_customer_queries(count=args.count, seed=args.seed))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    challenges = []
    if args.challenge_output:
        challenges = list(
            generate_customer_query_challenges(
                count=args.challenge_count, seed=args.seed + 1
            )
        )
        args.challenge_output.parent.mkdir(parents=True, exist_ok=True)
        args.challenge_output.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in challenges)
        )
    print(json.dumps({
        "rows": len(rows),
        "unique_questions": len({row["question"] for row in rows}),
        "language": "en",
        "intents": len({row['gold']['intent'] for row in rows}),
        "template_families": len({row["template_family"] for row in rows}),
        "challenge_rows": len(challenges),
        "challenge_unique_questions": len({row["question"] for row in challenges}),
    }))


if __name__ == "__main__":
    main()
