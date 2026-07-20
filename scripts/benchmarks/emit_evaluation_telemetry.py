#!/usr/bin/env python3
"""Export persisted SEOCHO evaluation artifacts to OTLP metrics and traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from seocho.eval.evaluation_telemetry import emit_query_evaluation, emit_scenario_status
from seocho.metrics import enable_metrics, shutdown_metrics
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span


def _load(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _require_otlp(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        raise ValueError(f"OTLP endpoint must include host and port: {endpoint}")
    with socket.create_connection((host, port), timeout=3):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    args = parser.parse_args()
    _require_otlp(args.otlp_grpc)
    os.environ["SEOCHO_TRACE_OTLP_ENDPOINT"] = args.otlp_grpc
    os.environ["OTEL_SERVICE_NAME"] = "seocho-evaluation"
    os.environ["OTEL_SERVICE_INSTANCE_ID"] = "evaluation-artifact-emitter"
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
    diverse = _load(root / "customer-query-diversity-v4-live-2026-07-12.json")
    diverse_bulk = _load(root / "customer-query-diverse-v4-bulk-live-10k-2026-07-12.json")
    intent_hybrid = _load(root / "customer-query-intent-hybrid-130-v4-final-live-2026-07-12.json")
    boundary_hybrid = _load(root / "customer-query-boundary-hybrid-300-v4-live-2026-07-12.json")
    answer_v4 = _load(root / "customer-query-answer-mara-50-v4-live-2026-07-12.json")
    artifact_digest = hashlib.sha256(
        b"".join(path.read_bytes() for path in sorted(root.glob("*.json")))
    ).hexdigest()[:16]
    run_status = "passed" if all(
        (
            s23["passed"], s67["passed"], s8["passed"], utility["passed"],
            text2cypher["passed"], mara["passed"], diverse["passed"],
            diverse_bulk["passed"], intent_hybrid["passed"],
            boundary_hybrid["passed"], answer_v4["passed"],
        )
    ) else "failed"
    with start_span(
        "evaluation.run",
        metadata={
            "seocho.evaluation.run.id": artifact_digest,
            "seocho.evaluation.status": run_status,
            "seocho.evaluation.query.count": routing["queries"],
            "seocho.evaluation.model": mara["model"],
        },
    ):
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
        metrics.set("seocho.evaluation.dataset.ratio", 1 - diverse["exact_duplicate_rate"], {"cohort": "customer-diverse-v4-10k", "dimension": "unique"})
        metrics.set("seocho.evaluation.dataset.ratio", diverse["routing_accuracy"]["held_out_family"], {"cohort": "customer-diverse-v4-10k", "dimension": "held_out_routing"})
        for cohort, report in (("intent-hybrid-130-v4", intent_hybrid), ("boundary-hybrid-300-v4", boundary_hybrid)):
            for group, result in report["by_group"].items():
                metrics.set("seocho.evaluation.intent.accuracy", result["action_accuracy"], {"cohort": cohort, "group": group, "dimension": "action"})
                metrics.set("seocho.evaluation.intent.accuracy", result["intent_accuracy"], {"cohort": cohort, "group": group, "dimension": "intent"})
        metrics.set("seocho.evaluation.answer.accuracy", answer_v4["status_accuracy"], {"cohort": "customer-diverse-v4-mara-50", "dimension": "support_status"})
        metrics.set("seocho.evaluation.answer.accuracy", answer_v4["missing_source_accuracy"], {"cohort": "customer-diverse-v4-mara-50", "dimension": "missing_sources"})
        metrics.record("seocho.evaluation.answer.latency", answer_v4["latency_ms"]["p95"], {"cohort": "customer-diverse-v4-mara-50"})
        metrics.add("seocho.evaluation.answer.leakage", answer_v4["leakage_cases"], {"cohort": "customer-diverse-v4-mara-50"})
        for outcome, count in diverse_bulk["outcomes"].items():
            metrics.set("seocho.evaluation.customer.outcome_ratio", count / diverse_bulk["queries"], {"outcome": f"v4_{outcome}"})
    flush_tracing()
    disable_tracing()
    shutdown_metrics()
    print(json.dumps({"exported": True, "run_id": artifact_digest, "status": run_status, "scenarios": 7, "queries": routing["queries"]}))


if __name__ == "__main__":
    main()
