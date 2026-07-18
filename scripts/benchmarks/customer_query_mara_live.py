#!/usr/bin/env python3
"""Bounded MARA answer cohort over the live customer-query source contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

from seocho.store.llm import MaraBackend
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span


async def run(args: argparse.Namespace) -> dict:
    rows = []
    with open(args.dataset, encoding="utf-8") as dataset_f:
        for line in dataset_f:
            if line.strip():
                rows.append(json.loads(line))
    bulk = json.loads(args.bulk_report.read_text())
    available = {
        source: bool(detail.get("available"))
        for source, detail in bulk["source_details"].items()
    }
    for source in (
        "postgresql_revision",
        "graph_projection",
        "order_history",
        "fill_history",
        "withdrawal_history",
        "counterparty_history",
        "funding_history",
        "answer_receipt",
        "context_graph",
    ):
        available[source] = True
    selected = []
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        intent = row["gold"]["intent"]
        if counts[intent] < args.per_intent:
            selected.append(row)
            counts[intent] += 1
    checkpoint: Path | None = getattr(args, "checkpoint", None)
    completed: dict[str, dict] = {}
    if checkpoint and checkpoint.exists():
        with open(checkpoint, encoding="utf-8") as ckpt_f:
            for line in ckpt_f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if query_id := item.get("query_id"):
                    completed[query_id] = item
        if getattr(args, "retry_failures", False):
            completed = {
                query_id: item
                for query_id, item in completed.items()
                if item.get("status_ok")
                and item.get("missing_ok")
                and not item.get("leakage")
            }
    resumed_queries = sum(row["query_id"] in completed for row in selected)
    backend = MaraBackend(
        model=args.model,
        timeout=float(getattr(args, "request_timeout", 120.0)),
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(row: dict) -> dict:
        sources = tuple(row["gold"]["live_sources"]) + tuple(
            row["gold"]["memory_sources"]
        )
        missing = sorted(
            source for source in sources if not available.get(source, False)
        )
        expected = "partial" if missing else "supported"
        async with semaphore:
            started = time.perf_counter()
            errors = []
            payload = None
            for attempt in range(1, 3):
                try:
                    response = await backend.acomplete(
                        system=(
                            "Answer an exchange customer using only the source-status evidence. "
                            "Return JSON keys support_status, answer, missing_sources. Preserve "
                            "the supplied authoritative_support_status exactly; its only values are "
                            "supported and partial. Never infer wallet ownership or identity."
                        ),
                        user=json.dumps(
                            {
                                "question": row["question"],
                                "authoritative_support_status": expected,
                                "available_sources": sorted(
                                    set(sources) - set(missing)
                                ),
                                "missing_sources": missing,
                            },
                            sort_keys=True,
                        ),
                        temperature=0.0,
                        max_tokens=800,
                        response_format={"type": "json_object"},
                        mode="pipeline",
                        model=args.model,
                    )
                    payload = response.json()
                    break
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if payload is None:
                return {
                    "query_id": row["query_id"],
                    "intent": row["gold"]["intent"],
                    "expected": expected,
                    "status_ok": False,
                    "missing_ok": False,
                    "leakage": False,
                    "attempts": 2,
                    "errors": errors,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            rendered = json.dumps(payload).lower()
            counterparty_intent = row["gold"]["intent"] in {
                "recipient_missing",
                "transfer_history",
            }
            leakage = counterparty_intent and any(
                phrase in rendered
                for phrase in (
                    "wallet belongs to",
                    "address belongs to",
                    "wallet is owned by",
                    "address is owned by",
                    "real identity",
                )
            )
            return {
                "query_id": row["query_id"],
                "intent": row["gold"]["intent"],
                "expected": expected,
                "answer_status": payload.get("support_status"),
                "status_ok": payload.get("support_status") == expected,
                "missing_ok": sorted(payload.get("missing_sources") or []) == missing,
                "leakage": leakage,
                "attempts": attempt,
                "errors": errors,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }

    pending = [row for row in selected if row["query_id"] not in completed]
    checkpoint_handle = None
    if checkpoint:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_handle = checkpoint.open("a", encoding="utf-8")
    try:
        for finished, task in enumerate(
            asyncio.as_completed(one(row) for row in pending), start=1
        ):
            result = await task
            completed[result["query_id"]] = result
            if checkpoint_handle:
                checkpoint_handle.write(json.dumps(result, sort_keys=True) + "\n")
                checkpoint_handle.flush()
            progress_every = int(getattr(args, "progress_every", 100))
            if progress_every > 0 and (
                finished % progress_every == 0 or finished == len(pending)
            ):
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "completed": resumed_queries + finished,
                            "total": len(selected),
                            "executed_this_run": finished,
                            "failures": sum(
                                not (row["status_ok"] and row["missing_ok"])
                                for row in completed.values()
                            ),
                        }
                    ),
                    flush=True,
                )
    finally:
        if checkpoint_handle:
            checkpoint_handle.close()
    results = [completed[row["query_id"]] for row in selected]
    latencies = sorted(row["latency_ms"] for row in results)
    passed = all(
        row["status_ok"] and row["missing_ok"] and not row["leakage"] for row in results
    )
    return {
        "schema_version": "seocho.customer-query-mara-live.v1",
        "model": args.model,
        "queries": len(results),
        "concurrency": args.concurrency,
        "resumed_queries": resumed_queries,
        "executed_queries": len(pending),
        "supported": sum(row["expected"] == "supported" for row in results),
        "partial": sum(row["expected"] == "partial" for row in results),
        "status_accuracy": sum(row["status_ok"] for row in results) / len(results),
        "missing_source_accuracy": sum(row["missing_ok"] for row in results)
        / len(results),
        "leakage_cases": sum(row["leakage"] for row in results),
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "p95": latencies[round((len(latencies) - 1) * 0.95)],
        },
        "rows": results,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--bulk-report", type=Path, required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--per-intent", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--otlp-grpc")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tracing_enabled = bool(args.otlp_grpc)
    if tracing_enabled:
        os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = args.otlp_grpc
        os.environ["OTEL_SERVICE_NAME"] = "seocho-customer-mara-eval"
        os.environ.setdefault("OTEL_SERVICE_INSTANCE_ID", "customer-mara-eval")
        enable_tracing(backend="otlp")
    try:
        with start_span(
            "customer_query.mara.run",
            metadata={
                "seocho.evaluation.model": args.model,
                "seocho.evaluation.per_intent": args.per_intent,
                "seocho.evaluation.concurrency": args.concurrency,
                "traffic.type": "evaluation",
            },
        ) as span:
            report = asyncio.run(run(args))
            span.set_output(
                {
                    "passed": report["passed"],
                    "queries": report["queries"],
                    "status_accuracy": report["status_accuracy"],
                    "missing_source_accuracy": report["missing_source_accuracy"],
                    "leakage_cases": report["leakage_cases"],
                }
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        rendered = (
            {key: value for key, value in report.items() if key != "rows"}
            if args.summary_only
            else report
        )
        print(json.dumps(rendered, indent=2, sort_keys=True))
    finally:
        if tracing_enabled:
            flush_tracing()
            disable_tracing()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
