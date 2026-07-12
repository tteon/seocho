#!/usr/bin/env python3
"""Run critical OKX scenarios under one real OTLP trace waterfall."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from scripts.benchmarks.customer_query_mara_live import run as run_mara
from scripts.benchmarks.okx_s2_s3_live import run as run_s2_s3
from scripts.benchmarks.okx_s6_s7_live import run as run_s6_s7
from scripts.benchmarks.okx_s8_reorg_live import run as run_s8
from scripts.benchmarks.okx_text2cypher_live import run as run_text2cypher
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span


async def _stage(
    name: str,
    operation: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    with start_span(
        f"okx.e2e.{name}",
        metadata={"seocho.e2e.stage": name, "traffic.type": "evaluation"},
    ) as span:
        value = operation()
        report = await value if hasattr(value, "__await__") else value
        elapsed_ms = (time.perf_counter() - started) * 1000
        span.set_output(
            {
                "passed": bool(report.get("passed")),
                "duration_ms": round(elapsed_ms, 3),
            }
        )
        span.set_metadata({"seocho.e2e.status": "passed" if report.get("passed") else "failed"})
        if not report.get("passed"):
            raise RuntimeError(f"live stage failed: {name}")
        return report, elapsed_ms


async def run(args: argparse.Namespace) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    durations: dict[str, float] = {}
    with start_span(
        "okx.e2e.run",
        metadata={
            "seocho.e2e.run.id": args.run_id,
            "seocho.e2e.domain": "blockchain-agent-transactions",
            "seocho.e2e.model": args.model,
            "traffic.type": "evaluation",
        },
    ) as root_span:
        reports["s2_s3"], durations["s2_s3"] = await _stage(
            "postgresql_concurrency_history", lambda: run_s2_s3(args.dsn)
        )
        reports["s6_s7"], durations["s6_s7"] = await _stage(
            "federation_etcd_governance",
            lambda: run_s6_s7(
                SimpleNamespace(
                    primary=args.bolt_uri,
                    secondary=args.secondary_bolt_uri,
                    unavailable=args.unavailable_bolt_uri,
                    password=args.graph_password,
                    etcd=args.etcd,
                    timeout=args.federation_timeout,
                )
            ),
        )
        reports["s8"], durations["s8"] = await _stage(
            "reorg_compensation_rebuild",
            lambda: run_s8(
                SimpleNamespace(
                    dsn=args.dsn,
                    bolt_uri=args.bolt_uri,
                    graph_password=args.graph_password,
                )
            ),
        )
        reports["text2cypher"], durations["text2cypher"] = await _stage(
            "validated_text2cypher",
            lambda: run_text2cypher(
                SimpleNamespace(
                    bolt_uri=args.bolt_uri,
                    graph_password=args.graph_password,
                    model=args.model,
                )
            ),
        )
        reports["mara"], durations["mara"] = await _stage(
            "mara_answer_contract",
            lambda: run_mara(
                SimpleNamespace(
                    dataset=args.dataset,
                    bulk_report=args.bulk_report,
                    model=args.model,
                    per_intent=1,
                    concurrency=args.llm_concurrency,
                )
            ),
        )
        root_span.set_output(
            {
                "passed": True,
                "stages": len(reports),
                "duration_ms": round(sum(durations.values()), 3),
            }
        )
        root_span.set_metadata({"seocho.e2e.status": "passed"})
    return {
        "schema_version": "seocho.okx-e2e-trace-live.v1",
        "run_id": args.run_id,
        "model": args.model,
        "source": "live-postgresql-dozerdb-etcd-mara",
        "stage_duration_ms": {key: round(value, 3) for key, value in durations.items()},
        "stage_status": {key: "passed" for key in reports},
        "mara": {
            "queries": reports["mara"]["queries"],
            "status_accuracy": reports["mara"]["status_accuracy"],
            "missing_source_accuracy": reports["mara"]["missing_source_accuracy"],
            "leakage_cases": reports["mara"]["leakage_cases"],
        },
        "text2cypher": {
            "attempts": reports["text2cypher"]["attempts"],
            "result_rows": reports["text2cypher"]["result_rows"],
        },
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("SEOCHO_E2E_DSN"), required=False)
    parser.add_argument("--graph-password", default=os.getenv("NEO4J_PASSWORD"), required=False)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--secondary-bolt-uri", default="bolt://127.0.0.1:7797")
    parser.add_argument("--unavailable-bolt-uri", default="bolt://127.0.0.1:57999")
    parser.add_argument("--etcd", default="http://127.0.0.1:52379")
    parser.add_argument("--federation-timeout", type=float, default=2.0)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--bulk-report", type=Path, required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--llm-concurrency", type=int, default=4)
    parser.add_argument("--run-id", default=f"okx-{int(time.time())}")
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.dsn or not args.graph_password:
        parser.error("--dsn/SEOCHO_E2E_DSN and --graph-password/NEO4J_PASSWORD are required")
    os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = args.otlp_grpc
    os.environ["OTEL_SERVICE_NAME"] = "seocho-okx-live"
    os.environ.setdefault("OTEL_SERVICE_INSTANCE_ID", "okx-e2e-live")
    enable_tracing(backend="otlp")
    try:
        report = asyncio.run(run(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        flush_tracing()
        disable_tracing()


if __name__ == "__main__":
    main()
