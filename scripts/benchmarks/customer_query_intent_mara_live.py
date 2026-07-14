#!/usr/bin/env python3
"""Evaluate MARA intent routing on diverse and boundary customer queries."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

from seocho.eval.customer_query_dataset import SEEDS, detect_customer_query_boundary
from seocho.store.llm import MaraBackend
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span

_INTENT_DEFINITIONS = {
    "order_status": "current lifecycle state of one exchange order, including filled and remaining quantity",
    "partial_fill": "why only part of an order executed and why quantity remains",
    "slippage": "difference between displayed or quoted price and average execution price",
    "withdrawal_pending": "an exchange withdrawal that has not completed or lacks network confirmations",
    "recipient_missing": "a sent transfer that the external recipient or destination has not credited",
    "transfer_history": "the user's previous transfers to a destination or counterparty",
    "account_history": "the user's deposits and withdrawals across their own funding account",
    "historical_order": "an order state or prior answer at a specific past revision, not its current state",
    "reorg_explanation": "a confirmed blockchain settlement reversed by an orphaned block or chain reorganization",
    "relevant_memory": "prior agent-memory revisions causally selected or excluded for a current decision",
}


async def run(args: argparse.Namespace) -> dict:
    clear_rows = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                clear_rows.append(json.loads(line))
    challenge_rows = []
    with open(args.challenges, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                challenge_rows.append(json.loads(line))
    selected = []
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in clear_rows:
        key = (row["gold"]["intent"], row["split"])
        if counts[key] < args.per_intent_split:
            selected.append(row)
            counts[key] += 1
    challenge_counts: dict[str, int] = defaultdict(int)
    for row in challenge_rows:
        kind = row["gold"]["kind"]
        if challenge_counts[kind] < args.per_challenge_kind:
            selected.append(row)
            challenge_counts[kind] += 1
    intent_ontology = [
        {
            "intent": seed.intent,
            "definition": _INTENT_DEFINITIONS[seed.intent],
            "relationship": seed.relationship,
            "required_evidence": list(seed.required_slots),
        }
        for seed in sorted(SEEDS, key=lambda item: item.intent)
    ]
    intents = [item["intent"] for item in intent_ontology]
    backend = MaraBackend(model=args.model)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(row: dict) -> dict:
        is_challenge = row["split"] == "challenge"
        expected_action = row["gold"]["expected_action"] if is_challenge else "route"
        expected_intents = (
            sorted(row["gold"]["acceptable_intents"])
            if is_challenge else [row["gold"]["intent"]]
        )
        boundary = detect_customer_query_boundary(row["question"])
        if boundary is not None:
            payload = {"action": boundary.action, "intents": list(boundary.intents)}
            attempt = 0
            errors: list[str] = []
            started = time.perf_counter()
        else:
            payload = None
            attempt = 0
            errors = []
            started = time.perf_counter()
        async with semaphore:
            for attempt in range(1, 3) if payload is None else ():
                try:
                    response = await backend.acomplete(
                        system=(
                            "Classify an exchange-support question. Return JSON with action and intents. "
                            "action must be route, clarify, decompose, or reject. Use route for one clear "
                            "intent, clarify when essential meaning is ambiguous, decompose for two explicit "
                            "requests, and reject for unsupported or privacy-invasive requests. intents must "
                            "contain only values from the supplied ontology. A transfer can mean an exchange "
                            "withdrawal or recipient delivery; clarify if the question does not distinguish them. "
                            "Account history means deposits/withdrawals on the user's account, while transfer "
                            "history is destination-specific. Historical order requires an explicit past time or "
                            "revision. Relevant memory concerns agent context selection, not blockchain reorgs. "
                            "Ignore contextual details that do not change the requested operation. Do not answer."
                        ),
                        user=json.dumps(
                            {"question": row["question"], "intent_ontology": intent_ontology},
                            sort_keys=True,
                        ),
                        temperature=0.0,
                        max_tokens=300,
                        response_format={"type": "json_object"},
                        mode="pipeline",
                        model=args.model,
                    )
                    payload = response.json()
                    break
                except Exception as exc:
                    errors.append(type(exc).__name__)
            observed_action = payload.get("action") if payload else None
            observed_intents = sorted(set(payload.get("intents") or [])) if payload else []
            valid_ontology = all(intent in intents for intent in observed_intents)
            action_ok = observed_action == expected_action
            intents_ok = observed_intents == expected_intents
            return {
                "query_id": row["query_id"],
                "split": row["split"],
                "kind": row["gold"].get("kind", "clear"),
                "expected_action": expected_action,
                "observed_action": observed_action,
                "expected_intents": expected_intents,
                "observed_intents": observed_intents,
                "action_ok": action_ok,
                "intents_ok": intents_ok,
                "valid_ontology": valid_ontology,
                "attempts": attempt if payload else 2,
                "errors": errors,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }

    rows = await asyncio.gather(*(one(row) for row in selected))
    latencies = sorted(row["latency_ms"] for row in rows)
    by_group = {}
    for group in sorted({row["kind"] if row["split"] == "challenge" else row["split"] for row in rows}):
        group_rows = [
            row for row in rows
            if (row["kind"] if row["split"] == "challenge" else row["split"]) == group
        ]
        by_group[group] = {
            "queries": len(group_rows),
            "action_accuracy": sum(row["action_ok"] for row in group_rows) / len(group_rows),
            "intent_accuracy": sum(row["intents_ok"] for row in group_rows) / len(group_rows),
        }
    thresholds = {
        "evaluation": ("intent_accuracy", 0.90),
        "held_out": ("intent_accuracy", 0.90),
        "ambiguous": ("action_accuracy", 0.90),
        "multi_intent": ("action_accuracy", 0.80),
        "out_of_scope": ("action_accuracy", 0.90),
    }
    passed = all(row["valid_ontology"] for row in rows) and all(
        group not in by_group or by_group[group][metric] >= minimum
        for group, (metric, minimum) in thresholds.items()
    )
    return {
        "schema_version": "seocho.customer-query-intent-mara-live.v1",
        "model": args.model,
        "queries": len(rows),
        "concurrency": args.concurrency,
        "by_group": by_group,
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "p95": latencies[round((len(latencies) - 1) * 0.95)],
        },
        "invalid_ontology_cases": sum(not row["valid_ontology"] for row in rows),
        "rows": rows,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--challenges", type=Path, required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--per-intent-split", type=int, default=5)
    parser.add_argument("--per-challenge-kind", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--otlp-grpc")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tracing_enabled = bool(args.otlp_grpc)
    if tracing_enabled:
        os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = args.otlp_grpc
        os.environ["OTEL_SERVICE_NAME"] = "seocho-customer-intent-eval"
        os.environ["OTEL_SERVICE_INSTANCE_ID"] = "customer-intent-eval"
        enable_tracing(backend="otlp")
    try:
        with start_span("customer_query.intent.run", metadata={"traffic.type": "evaluation"}):
            report = asyncio.run(run(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2, sort_keys=True))
    finally:
        if tracing_enabled:
            flush_tracing()
            disable_tracing()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
