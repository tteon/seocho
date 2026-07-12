#!/usr/bin/env python3
"""Export persisted SEOCHO evaluation artifacts to OTLP metrics and traces."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from seocho.eval.evaluation_telemetry import emit_query_evaluation, emit_scenario_status
from seocho.metrics import enable_metrics, shutdown_metrics
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    args = parser.parse_args()
    os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = args.otlp_grpc
    os.environ["OTEL_SERVICE_NAME"] = "seocho-evaluation"
    metrics = enable_metrics(backend="otlp", endpoint=args.otlp_grpc)
    enable_tracing(backend="otlp")
    root = args.artifacts
    s23 = _load(root / "okx-s2-s3-live-2026-07-12.json")
    s67 = _load(root / "okx-s6-s7-live-2026-07-12.json")
    s8 = _load(root / "okx-s8-reorg-live-2026-07-12.json")
    tls = _load(root / "dozerdb-tls-capability-2026-07-12.json")
    utility = _load(root / "okx-memory-utility-live-2026-07-12.json")
    text2cypher = _load(root / "okx-text2cypher-live-2026-07-12.json")
    routing = _load(root / "customer-query-routing-10k-2026-07-12.json")
    mara = _load(root / "customer-query-mara-10-live-2026-07-12.json")
    bulk = _load(root / "customer-query-bulk-live-10k-2026-07-12.json")
    for scenario_id, passed in (
        ("S2", s23["s2"]["passed"]), ("S3", s23["s3"]["passed"]),
        ("S5", utility["passed"]), ("S6", s67["s6"]["passed"]),
        ("S7", s67["s7"]["passed"]), ("S8", s8["passed"]),
    ):
        emit_scenario_status(scenario_id, status="passed" if passed else "failed", metrics=metrics)
    emit_scenario_status("S10", status=tls["status"], metrics=metrics)
    metrics.set("seocho.evaluation.capability.status", int(tls["passed"]), {"capability": "tls_reload", "status": tls["status"]})
    metrics.set("seocho.evaluation.context.reduction", utility["context_ab"]["estimated_token_reduction"], {"strategy": "causal"})
    metrics.record("seocho.evaluation.text2cypher.attempts", text2cypher["attempts"], {"outcome": "passed" if text2cypher["passed"] else "failed"})
    emit_query_evaluation(cohort="customer-template-10k", total=routing["queries"], correct=routing["queries"] - routing["errors"], metrics=metrics)
    metrics.set("seocho.evaluation.answer.accuracy", mara["status_accuracy"], {"cohort": "customer-mara-10", "dimension": "support_status"})
    metrics.set("seocho.evaluation.answer.accuracy", mara["missing_source_accuracy"], {"cohort": "customer-mara-10", "dimension": "missing_sources"})
    metrics.record("seocho.evaluation.answer.latency", mara["latency_ms"]["p95"], {"cohort": "customer-mara-10"})
    metrics.add("seocho.evaluation.answer.leakage", mara["leakage_cases"], {"cohort": "customer-mara-10"})
    for outcome, count in bulk["outcomes"].items():
        metrics.set("seocho.evaluation.customer.outcome_ratio", count / bulk["queries"], {"outcome": outcome})
    for query_class, outcomes in bulk["by_intent"].items():
        supported = outcomes.get("supported", 0)
        partial = outcomes.get("partial", 0)
        unsupported = outcomes.get("unsupported", 0)
        coverage = (supported + 0.5 * partial) / max(supported + partial + unsupported, 1)
        metrics.set("seocho.evaluation.customer.evidence_coverage", coverage, {"query.class": query_class})
    flush_tracing()
    disable_tracing()
    shutdown_metrics()
    print(json.dumps({"exported": True, "scenarios": 7, "queries": routing["queries"]}))


if __name__ == "__main__":
    main()
