#!/usr/bin/env python3
"""Generate a deterministic multi-agent OKX demo/replay transaction corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.agent_transaction_dataset import generate_agent_transaction_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for event in generate_agent_transaction_events(
            transaction_count=args.transactions
        ):
            stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
