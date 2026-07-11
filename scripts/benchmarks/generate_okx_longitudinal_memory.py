#!/usr/bin/env python3
"""Write deterministic JSONL events and gold queries for the OKX memory spec."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.longitudinal_memory import build_gold_queries, generate_longitudinal_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gold-output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.gold_output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for event in generate_longitudinal_events(event_count=args.events, seed=args.seed):
            stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
    with args.gold_output.open("w", encoding="utf-8") as stream:
        for query in build_gold_queries(final_sequence=args.events):
            stream.write(json.dumps(query.to_dict(), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
