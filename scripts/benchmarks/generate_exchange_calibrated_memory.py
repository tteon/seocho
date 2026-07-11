#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from seocho.eval.exchange_calibrated import (
    DEFAULT_SCENARIO_WEIGHTS,
    generate_exchange_calibrated_events,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intents", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    counts: Counter[str] = Counter()
    events = 0
    started = time.perf_counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for event in generate_exchange_calibrated_events(intent_count=args.intents, seed=args.seed):
            stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            counts[event.scenario] += int(event.step == "intent")
            events += 1
    elapsed = time.perf_counter() - started
    manifest = {
        "schema_version": "exchange-calibrated-agent-memory-manifest.v1",
        "seed": args.seed,
        "intents": args.intents,
        "events": events,
        "output_bytes": args.output.stat().st_size,
        "generation_seconds": round(elapsed, 6),
        "events_per_second": round(events / elapsed, 2),
        "scenario_intents": dict(sorted(counts.items())),
        "scenario_weight_basis_points": DEFAULT_SCENARIO_WEIGHTS,
        "evidence_policy": "synthetic frequencies are hypotheses; venue semantics are documented",
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
