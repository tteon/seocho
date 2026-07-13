#!/usr/bin/env python3
"""Live Cognee V1 temporal-memory qualification on blockchain event history."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from seocho.eval.longitudinal_memory import generate_longitudinal_events


def _event_text(event: Any) -> str:
    return (
        f"At {event.occurred_at}, transaction {event.transaction_ref} on "
        f"{event.chain_id} changed to state {event.state}. It involved agent "
        f"{event.agent_ref} and counterparty {event.counterparty_ref}. The block "
        f"height was {event.block_height}. Provenance is {event.provenance_id}."
    )


def _render(value: Any) -> str:
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except TypeError:
        return str(value)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    # Import after shell/runtime configuration is present: Cognee materializes
    # provider settings during import.
    import cognee
    from cognee.api.v1.search import SearchType

    if not os.getenv("LLM_API_KEY"):
        raise RuntimeError("LLM_API_KEY is required (use MARA_API_KEY)")
    cognee.config.set("llm_provider", args.provider)
    cognee.config.set("llm_model", args.model)
    cognee.config.set("llm_endpoint", args.endpoint)
    cognee.config.set("embedding_provider", "fastembed")
    cognee.config.set("embedding_model", args.embedding_model)
    cognee.config.set("embedding_dimensions", 384)

    events = tuple(
        generate_longitudinal_events(
            event_count=args.events,
            seed=args.seed,
            workspace_id=f"cognee-blockchain-{args.seed}",
        )
    )
    histories: dict[str, list[Any]] = {}
    for event in events:
        histories.setdefault(event.transaction_ref, []).append(event)
    selected = [histories[key] for key in sorted(histories)[: args.sample_memories]]
    corpus = "\n".join(_event_text(event) for event in events)

    await cognee.prune.prune_system(metadata=True)
    ingest_started = time.perf_counter()
    remember_result = await cognee.remember(
        corpus,
        dataset_name=args.dataset,
        temporal_cognify=True,
        self_improvement=False,
    )
    ingest_seconds = time.perf_counter() - ingest_started

    rows = []
    query_type = SearchType[args.query_type]
    for history in selected:
        current = history[-1]
        prior = history[-2] if len(history) > 1 else None
        cases = [
            (
                "current",
                f"What is the latest recorded state of transaction {current.transaction_ref}?",
                current.state,
            )
        ]
        if prior:
            cases.append(
                (
                    "historical",
                    f"What was the state of transaction {prior.transaction_ref} at "
                    f"{prior.occurred_at}?",
                    prior.state,
                )
            )
        for family, question, expected in cases:
            started = time.perf_counter()
            result = await cognee.recall(
                query_text=question,
                query_type=query_type,
                datasets=[args.dataset],
                top_k=10,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            rendered = _render(result).lower()
            rows.append(
                {
                    "family": family,
                    "question": question,
                    "expected_state": expected,
                    "contains_expected_state": expected.lower() in rendered,
                    "private_metadata_leakage": "never-export" in rendered
                    or "internal_note" in rendered,
                    "latency_ms": latency_ms,
                    "response": result,
                }
            )
    latencies = sorted(row["latency_ms"] for row in rows)
    correct = sum(row["contains_expected_state"] for row in rows)
    return {
        "schema_version": "seocho.cognee-blockchain-memory-live.v1",
        "framework": "cognee",
        "framework_version": version("cognee"),
        "source": "live-cognee-mara-fastembed",
        "dataset": {
            "events": len(events),
            "logical_memories": len(histories),
            "sample_memories": len(selected),
            "seed": args.seed,
            "synthetic": True,
        },
        "provider": {
            "llm": f"mara/{args.model}",
            "endpoint": args.endpoint,
            "embedding": args.embedding_model,
            "embedding_dimensions": 384,
            "query_type": args.query_type,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "ingestion": {
            "seconds": ingest_seconds,
            "events_per_second": len(events) / ingest_seconds,
            "result": remember_result,
        },
        "retrieval": {
            "cases": len(rows),
            "correct": correct,
            "accuracy": correct / len(rows) if rows else None,
            "leakage_cases": sum(row["private_metadata_leakage"] for row in rows),
            "p50_ms": latencies[len(latencies) // 2] if latencies else 0,
            "p95_ms": (
                latencies[min(round((len(latencies) - 1) * 0.95), len(latencies) - 1)]
                if latencies
                else 0
            ),
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=30)
    parser.add_argument("--sample-memories", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--dataset", default="seocho-cognee-blockchain")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--provider", default="custom")
    parser.add_argument(
        "--query-type",
        choices=("TEMPORAL", "GRAPH_COMPLETION"),
        default="TEMPORAL",
    )
    parser.add_argument("--endpoint", default="https://api.cloud.mara.com/v1")
    parser.add_argument(
        "--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, default=str, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "rows"},
            default=str,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
