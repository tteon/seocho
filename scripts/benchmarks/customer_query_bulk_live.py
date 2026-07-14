#!/usr/bin/env python3
"""Execute the 10K customer-query corpus against live source capabilities."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from neo4j import GraphDatabase

from seocho.eval.customer_query_dataset import classify_customer_query
from seocho.metrics import enable_metrics, shutdown_metrics


def _json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def _text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return response.read().decode().strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--graph-password", required=True)
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["OTEL_SERVICE_NAME"] = "seocho-customer-query-eval"
    metrics = enable_metrics(backend="otlp", endpoint=args.otlp_grpc)
    snapshot_at = time.time()
    sources: dict[str, bool] = {}
    source_details: dict[str, object] = {}
    market_errors = []
    sources["market_api"] = False
    for provider, url in (
        ("okx", "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT"),
        ("coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
    ):
        try:
            ticker = _json(url)
            available = bool(ticker.get("data"))
            if available:
                sources["market_api"] = True
                source_details["market_api"] = {
                    "instrument": "BTC spot", "provider": provider, "available": True
                }
                break
        except Exception as exc:
            market_errors.append({"provider": provider, "error": type(exc).__name__})
    if not sources["market_api"]:
        source_details["market_api"] = {"available": False, "attempts": market_errors}
    try:
        height = int(_text("https://blockstream.info/api/blocks/tip/height"))
        sources["blockchain_api"] = height > 0
        source_details["blockchain_api"] = {"tip_height": height, "available": True}
    except Exception as exc:
        sources["blockchain_api"] = False
        source_details["blockchain_api"] = {"available": False, "error": type(exc).__name__}
    with psycopg.connect(args.dsn) as connection:
        sources["postgresql_revision"] = connection.execute("SELECT 1").fetchone()[0] == 1
    driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.graph_password))
    try:
        sources["graph_projection"] = driver.execute_query("RETURN 1 AS ok").records[0]["ok"] == 1
    finally:
        driver.close()
    for source in (
        "order_api", "withdrawal_api", "transfer_api",
    ):
        sources[source] = False
        source_details[source] = {"available": False, "reason": "private_credentials_not_configured"}
    for source in ("order_history", "fill_history", "withdrawal_history", "counterparty_history", "funding_history", "answer_receipt", "context_graph"):
        sources[source] = sources["postgresql_revision"]

    rows = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    outcomes: Counter[str] = Counter()
    by_intent: dict[str, Counter[str]] = defaultdict(Counter)
    durations = []
    coverage_values = []
    for row in rows:
        started = time.perf_counter()
        routed = classify_customer_query(row["question"])
        gold = row["gold"]
        required_sources = tuple(gold["live_sources"]) + tuple(gold["memory_sources"])
        available = sum(bool(sources.get(source)) for source in required_sources)
        coverage = available / len(required_sources) if required_sources else 1.0
        routing_ok = routed is not None and routed.intent == gold["intent"]
        outcome = "supported" if routing_ok and coverage == 1 else "partial" if routing_ok and coverage > 0 else "unsupported"
        elapsed = time.perf_counter() - started
        outcomes[outcome] += 1
        by_intent[gold["intent"]][outcome] += 1
        durations.append(elapsed)
        coverage_values.append(coverage)
        labels = {"query.class": gold["intent"], "outcome": outcome, "traffic.type": "evaluation"}
        metrics.add("seocho.customer.query.count", attributes=labels)
        metrics.record("seocho.customer.query.duration", elapsed, labels)
        metrics.record("seocho.customer.evidence.coverage", coverage, {"query.class": gold["intent"]})
    age = max(time.time() - snapshot_at, 0)
    for source in ("market_api", "blockchain_api"):
        if sources[source]:
            metrics.record("seocho.customer.source.freshness", age, {"source": source})
        else:
            metrics.add("seocho.customer.source.failure.count", attributes={"source": source, "reason": "unavailable"})
    shutdown_metrics()
    report = {
        "schema_version": "seocho.customer-query-bulk-live.v1",
        "queries": len(rows),
        "outcomes": dict(outcomes),
        "by_intent": {key: dict(value) for key, value in sorted(by_intent.items())},
        "mean_evidence_coverage": sum(coverage_values) / len(coverage_values),
        "duration_ms": {"mean": sum(durations) * 1000 / len(durations), "max": max(durations) * 1000},
        "source_details": source_details,
        "private_sources_configured": False,
        "passed": len(rows) == sum(outcomes.values())
        and outcomes["unsupported"] == 0
        and sources["market_api"]
        and sources["blockchain_api"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
