#!/usr/bin/env python3
"""Run SEOCHO and peer stores on one deterministic blockchain memory corpus."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from memory_framework_adapters import (
    LangGraphPostgresAdapter,
    SeochoPostgresAdapter,
)
from seocho.eval.longitudinal_memory import generate_longitudinal_events
from seocho.eval.memory_framework_benchmark import (
    build_temporal_cases,
    qualify_adapter,
)


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--sample-memories", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--frameworks", default="seocho,langgraph")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events = tuple(
        generate_longitudinal_events(
            event_count=args.events,
            seed=args.seed,
            workspace_id=f"memory-framework-{args.seed}",
        )
    )
    cases = build_temporal_cases(events, sample_memories=args.sample_memories)
    reports = []
    for framework in args.frameworks.split(","):
        workspace = f"memory-framework-{framework}-{args.seed}"
        if framework == "seocho":
            adapter = SeochoPostgresAdapter(args.dsn, workspace)
        elif framework == "langgraph":
            adapter = LangGraphPostgresAdapter(args.dsn, workspace)
        else:
            raise ValueError(f"unknown framework: {framework}")
        try:
            reports.append(qualify_adapter(adapter, events, cases))
        finally:
            close = getattr(adapter, "close", None)
            if close:
                close()
    report = {
        "schema_version": "seocho.memory-framework-comparison-live.v1",
        "source": "live-postgresql-structured-blockchain-memory",
        "provider": {
            "answer_llm": "mara/MiniMax-M2.7",
            "answer_generation_executed": False,
            "embedding": "none-structured-state-baseline",
            "mara_embedding_capability": "unsupported-probed-http-400",
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": {
                name: _version(name)
                for name in ("seocho", "langgraph", "langgraph-checkpoint-postgres")
            },
        },
        "dataset": {
            "events": len(events),
            "sample_memories": args.sample_memories,
            "seed": args.seed,
            "synthetic": True,
            "domain": "bitcoin-agent-transaction-lifecycle",
        },
        "frameworks": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                **report,
                "frameworks": [
                    {key: value for key, value in item.items() if key != "rows"}
                    for item in reports
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
