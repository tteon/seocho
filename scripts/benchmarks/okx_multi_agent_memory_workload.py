#!/usr/bin/env python3
"""Run SDCR evidence agents alongside the live PostgreSQL memory workload."""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import random
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import psycopg
from psycopg_pool import ConnectionPool

from okx_long_term_memory_mixed_load import (
    MIX,
    Runner,
    drain_projection,
    prepare,
    snapshot,
)
from seocho.memory import PostgreSQLMemoryRepository
from seocho.metrics import enable_metrics, shutdown_metrics
from seocho.query.evidence_swarm import (
    EvidenceSwarmCoordinator,
    EvidenceSwarmRequest,
)
from seocho.query.sdcr import Capability, Evidence
from seocho.tracing import disable_tracing, enable_tracing, flush_tracing, start_span

AGENT_MIX = {
    "agent.current_provenance": 12,
    "agent.historical_compare": 10,
    "agent.projection_consistency": 8,
}
MEMORY_WEIGHT = 0.70


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(round((len(ordered) - 1) * p), len(ordered) - 1)]


def state_of(revision: Any) -> str:
    return str(revision.payload.get("state") or revision.event_type.rsplit(".", 1)[-1])


@dataclass(slots=True)
class AuthoritySpecialist:
    repository: PostgreSQLMemoryRepository
    capability: Capability = Capability(
        "authoritative-memory",
        frozenset({"authoritative_state", "state_consistency_candidate"}),
        priority=40,
    )

    def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
        memory_id = str(request.context["memory_id"])
        revision = self.repository.read_revision(
            workspace_id=request.workspace_id, memory_id=memory_id
        )
        if revision is None:
            return ()
        state = state_of(revision)
        provenance = {"sequence": revision.sequence, "revision": revision.revision}
        return (
            Evidence(
                f"revision:{revision.sequence}",
                self.capability.view_id,
                "authoritative_state",
                {"state": state, "canonical": revision.canonical},
                provenance=provenance,
            ),
            Evidence(
                f"revision-state:{revision.sequence}",
                self.capability.view_id,
                "state_consistency_candidate",
                state,
                provenance=provenance,
            ),
        )


@dataclass(slots=True)
class HistoricalSpecialist:
    repository: PostgreSQLMemoryRepository
    capability: Capability = Capability(
        "historical-memory", frozenset({"historical_state"}), priority=30
    )

    def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
        memory_id = str(request.context["memory_id"])
        history = self.repository.read_history(
            workspace_id=request.workspace_id,
            memory_id=memory_id,
            through_sequence=int(request.context["latest_sequence"]),
            limit=2,
        )
        if len(history) < 2:
            return ()
        revision = history[1]
        return (
            Evidence(
                f"historical-revision:{revision.sequence}",
                self.capability.view_id,
                "historical_state",
                {
                    "state": state_of(revision),
                    "sequence": revision.sequence,
                    "canonical": revision.canonical,
                },
                provenance={
                    "sequence": revision.sequence,
                    "revision": revision.revision,
                },
            ),
        )


@dataclass(slots=True)
class ProjectionSpecialist:
    pool: ConnectionPool
    capability: Capability = Capability(
        "graph-projection",
        frozenset(
            {
                "projected_state",
                "projection_freshness",
                "state_consistency_candidate",
            }
        ),
        priority=20,
    )

    def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
        memory_id = str(request.context["memory_id"])
        required_sequence = int(request.context["latest_sequence"])
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT p.sequence,p.state,w.sequence "
                "FROM agent_memory_projection_mixed_shadow p "
                "JOIN agent_memory_projection_mixed_watermark w USING(workspace_id) "
                "WHERE p.workspace_id=%s AND p.memory_id=%s",
                (request.workspace_id, memory_id),
            ).fetchone()
        if row is None:
            return ()
        projected_sequence, state, watermark = int(row[0]), str(row[1]), int(row[2])
        current = (
            projected_sequence >= required_sequence and watermark >= required_sequence
        )
        provenance = {
            "sequence": projected_sequence,
            "watermark": watermark,
            "required_sequence": required_sequence,
        }
        return (
            Evidence(
                f"projection:{projected_sequence}",
                self.capability.view_id,
                "projected_state",
                {"state": state, "sequence": projected_sequence},
                provenance=provenance,
            ),
            Evidence(
                f"projection-watermark:{watermark}",
                self.capability.view_id,
                "projection_freshness",
                "current" if current else "stale",
                provenance=provenance,
            ),
            Evidence(
                f"projection-state:{projected_sequence}",
                self.capability.view_id,
                "state_consistency_candidate",
                state,
                provenance=provenance,
            ),
        )


@dataclass(slots=True)
class ProvenanceSpecialist:
    repository: PostgreSQLMemoryRepository
    capability: Capability = Capability(
        "provenance-auditor", frozenset({"provenance", "raw_payload"}), priority=10
    )

    def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
        memory_id = str(request.context["memory_id"])
        revision = self.repository.read_revision(
            workspace_id=request.workspace_id, memory_id=memory_id
        )
        if revision is None:
            return ()
        provenance = {
            "sequence": revision.sequence,
            "revision": revision.revision,
            "canonical": revision.canonical,
        }
        return (
            Evidence(
                f"provenance:{revision.sequence}",
                self.capability.view_id,
                "provenance",
                {"provenance_id": revision.provenance_id, **provenance},
                provenance=provenance,
            ),
            Evidence(
                f"protected-payload:{revision.sequence}",
                self.capability.view_id,
                "raw_payload",
                dict(revision.payload),
                protected=True,
                provenance=provenance,
            ),
        )


SCENARIOS = {
    "agent.current_provenance": (
        "current_memory_with_provenance",
        "What is this transaction's current state and which revision proves it?",
        ("authoritative_state", "provenance"),
    ),
    "agent.historical_compare": (
        "historical_memory_compare",
        "What changed between the historical and current transaction state?",
        ("authoritative_state", "historical_state", "provenance"),
    ),
    "agent.projection_consistency": (
        "projection_consistency",
        "Do authoritative memory and the graph projection agree and is it fresh?",
        (
            "authoritative_state",
            "projected_state",
            "projection_freshness",
            "provenance",
        ),
    ),
}


def make_request(
    operation: str,
    workspace: str,
    memory_id: str,
    latest_sequence: int,
) -> EvidenceSwarmRequest:
    intent_id, question, required_slots = SCENARIOS[operation]
    return EvidenceSwarmRequest(
        workspace_id=workspace,
        intent_id=intent_id,
        question=question,
        required_slots=required_slots,
        context={
            "memory_id": memory_id,
            "latest_sequence": latest_sequence,
        },
    )


def execute_agent_query(
    coordinator: EvidenceSwarmCoordinator,
    operation: str,
    workspace: str,
    sample: tuple[str, int],
) -> tuple[str, float, str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        request = make_request(operation, workspace, *sample)
        result = coordinator.run(request)
        detail = "ok" if result.status == "complete" else result.status
        metadata = {
            "status": result.status,
            "selected_agents": len(result.receipt.selected_views),
            "selected_views": list(result.receipt.selected_views),
            "safe_evidence": len(result.bundle.selected_evidence),
            "protected_filtered": result.bundle.protected_evidence_count,
            "missing_slots": len(result.bundle.missing_slots),
            "conflicts": len(result.bundle.conflict_verification.get("conflicts", [])),
            "specialist_latency_ms": {
                run.view_id: round(run.latency_ms, 3) for run in result.specialist_runs
            },
        }
        return operation, (time.perf_counter() - started) * 1000, detail, metadata
    except Exception as exc:
        return (
            operation,
            (time.perf_counter() - started) * 1000,
            f"{type(exc).__name__}: {exc}",
            {},
        )


def operation_population() -> tuple[list[str], list[float]]:
    operations: list[str] = []
    weights: list[float] = []
    for name, weight in MIX.items():
        operations.append(f"memory.{name}")
        weights.append(weight * MEMORY_WEIGHT)
    for name, weight in AGENT_MIX.items():
        operations.append(name)
        weights.append(float(weight))
    return operations, weights


def run_mixed_phase(
    *,
    runner: Runner,
    coordinator: EvidenceSwarmCoordinator,
    workspace: str,
    samples: Sequence[tuple[str, int]],
    operations: int,
    concurrency: int,
    seed: int,
) -> dict[str, Any]:
    names, weights = operation_population()
    rows: list[tuple[str, float, str, dict[str, Any]]] = []

    def execute(task_id: int) -> tuple[str, float, str, dict[str, Any]]:
        rng = random.Random(seed + task_id * 104729)
        operation = rng.choices(names, weights=weights, k=1)[0]
        sample = samples[rng.randrange(len(samples))]
        if operation.startswith("memory."):
            memory_operation = operation.removeprefix("memory.")
            name, latency, expected, detail = runner.execute(memory_operation, task_id)
            if expected and detail in {"idempotent_replay", "transition_rejected"}:
                detail = "ok"
            return f"memory.{name}", latency, detail, {}
        return execute_agent_query(coordinator, operation, workspace, sample)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(contextvars.copy_context().run, execute, task_id)
            for task_id in range(operations)
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    elapsed = time.perf_counter() - started

    grouped: dict[str, list[tuple[float, str, dict[str, Any]]]] = defaultdict(list)
    for operation, latency, detail, metadata in rows:
        grouped[operation].append((latency, detail, metadata))
    by_operation: dict[str, Any] = {}
    unexpected_errors = 0
    for operation in names:
        values = grouped[operation]
        latencies = [value[0] for value in values]
        errors = [value[1] for value in values if value[1] not in {"ok", "partial"}]
        unexpected_errors += len(errors)
        by_operation[operation] = {
            "count": len(values),
            "partial": sum(value[1] == "partial" for value in values),
            "unexpected_errors": len(errors),
            "error_samples": errors[:3],
            "latency_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "max": max(latencies, default=0.0),
            },
        }
    agent_metadata = [
        metadata
        for operation, _latency, _detail, metadata in rows
        if operation.startswith("agent.") and metadata
    ]
    specialist_latencies: dict[str, list[float]] = defaultdict(list)
    for metadata in agent_metadata:
        for view_id, latency in metadata["specialist_latency_ms"].items():
            specialist_latencies[view_id].append(float(latency))
    return {
        "operations": operations,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "operations_per_second": operations / elapsed,
        "unexpected_errors": unexpected_errors,
        "by_operation": by_operation,
        "agent_summary": {
            "queries": len(agent_metadata),
            "multi_agent_queries": sum(
                item["selected_agents"] > 1 for item in agent_metadata
            ),
            "coalition_size": dict(
                sorted(
                    Counter(item["selected_agents"] for item in agent_metadata).items()
                )
            ),
            "safe_evidence": sum(item["safe_evidence"] for item in agent_metadata),
            "protected_filtered": sum(
                item["protected_filtered"] for item in agent_metadata
            ),
            "missing_slots": sum(item["missing_slots"] for item in agent_metadata),
            "conflicts": sum(item["conflicts"] for item in agent_metadata),
            "specialist_latency_ms": {
                view_id: {
                    "p50": percentile(values, 0.50),
                    "p95": percentile(values, 0.95),
                    "p99": percentile(values, 0.99),
                }
                for view_id, values in sorted(specialist_latencies.items())
            },
        },
    }


class MaraSynthesizer:
    """Bounded live answerer; raw prompts/completions never enter the report."""

    SYSTEM = (
        "You are an exchange transaction support agent. Answer only from the "
        "provided typed evidence. State missing slots or conflicts explicitly. "
        "Never reveal raw payload fields. Keep the answer under 100 words."
    )

    def __init__(self, model: str) -> None:
        from openai import OpenAI

        key = os.getenv("MARA_API_KEY", "").strip()
        if not key:
            raise RuntimeError("MARA_API_KEY is not configured")
        self.client = OpenAI(api_key=key, base_url="https://api.cloud.mara.com/v1")
        self.model = model
        self.last_usage: dict[str, Any] = {}

    def __call__(self, request: EvidenceSwarmRequest, bundle: Any) -> str:
        started = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            top_p=0.1,
            max_tokens=180,
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": request.question,
                            "evidence": bundle.as_dict(),
                        },
                        sort_keys=True,
                        default=str,
                    ),
                },
            ],
        )
        usage = response.usage
        self.last_usage = {
            "latency_ms": (time.perf_counter() - started) * 1000,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        return str(response.choices[0].message.content or "").strip()


def run_mara_samples(
    *,
    coordinator: EvidenceSwarmCoordinator,
    workspace: str,
    samples: Sequence[tuple[str, int]],
    count: int,
    model: str,
) -> dict[str, Any]:
    if count <= 0:
        return {"status": "skipped", "reason": "llm_cases=0", "cases": []}
    cases = []
    try:
        synthesizer = MaraSynthesizer(model)
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "cases": [],
        }
    for index in range(count):
        operation = tuple(AGENT_MIX)[index % len(AGENT_MIX)]
        request = make_request(operation, workspace, *samples[index % len(samples)])
        started = time.perf_counter()
        try:
            result = coordinator.run(request, synthesizer=synthesizer)
            cases.append(
                {
                    "intent_id": request.intent_id,
                    "status": result.status,
                    "answer_present": bool(result.answer),
                    "answer_chars": len(result.answer),
                    "selected_agents": len(result.receipt.selected_views),
                    "safe_evidence": len(result.bundle.selected_evidence),
                    "missing_slots": len(result.bundle.missing_slots),
                    "total_latency_ms": (time.perf_counter() - started) * 1000,
                    **synthesizer.last_usage,
                }
            )
        except Exception as exc:
            cases.append(
                {
                    "intent_id": request.intent_id,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "answer_present": False,
                }
            )
    complete = all(
        case.get("status") in {"complete", "partial"} and case.get("answer_present")
        for case in cases
    )
    return {
        "status": "complete" if complete else "failed",
        "model": model,
        "cases": cases,
    }


def dataset_metadata(dsn: str, workspace: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        version = str(connection.execute("SHOW server_version").fetchone()[0])
        revisions, memories, maximum = connection.execute(
            "SELECT count(*),count(DISTINCT memory_id),max(sequence) "
            "FROM agent_memory_revisions WHERE workspace_id=%s",
            (workspace,),
        ).fetchone()
        source_rows = connection.execute(
            "SELECT coalesce(payload->>'source','unspecified'),count(*) "
            "FROM agent_memory_revisions WHERE workspace_id=%s GROUP BY 1 "
            "ORDER BY count(*) DESC LIMIT 10",
            (workspace,),
        ).fetchall()
    return {
        "postgresql_version": version,
        "workspace": workspace,
        "revisions": int(revisions),
        "memories": int(memories),
        "max_sequence": int(maximum),
        "payload_sources": {str(name): int(count) for name, count in source_rows},
    }


def longitudinal_samples(dsn: str, workspace: str) -> list[tuple[str, int]]:
    """Return memories that can answer an actual historical comparison."""

    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            "SELECT memory_id,max(sequence) FROM agent_memory_revisions "
            "WHERE workspace_id=%s GROUP BY memory_id HAVING count(*)>=2 "
            "ORDER BY max(sequence) DESC LIMIT 10000",
            (workspace,),
        ).fetchall()
    if not rows:
        raise RuntimeError(f"workspace {workspace!r} has no longitudinal memories")
    return [(str(memory_id), int(sequence)) for memory_id, sequence in rows]


def drain_all(dsn: str, workspace: str) -> dict[str, Any]:
    status = snapshot(dsn, workspace)
    initial_lag = status["projection_lag_events"]
    batches = 0
    started = time.perf_counter()
    while status["projection_lag_events"] > 0 and batches < 1000:
        drain_projection(dsn, workspace)
        batches += 1
        status = snapshot(dsn, workspace)
    return {
        "initial_lag_events": initial_lag,
        "batches": batches,
        "rto_seconds": time.perf_counter() - started,
        "final_lag_events": status["projection_lag_events"],
        "snapshot": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--operations", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--repository-pool-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--llm-cases", type=int, default=3)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--otlp-grpc", default="http://127.0.0.1:54317")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.operations < 1 or args.concurrency < 1:
        parser.error("operations and concurrency must be positive")

    os.environ["OTEL_SERVICE_NAME"] = "seocho-okx-multi-agent-memory"
    os.environ["OTEL_SERVICE_INSTANCE_ID"] = uuid.uuid4().hex[:12]
    enable_tracing(backend="otlp", endpoint=args.otlp_grpc)
    enable_metrics(backend="otlp", endpoint=args.otlp_grpc)
    runner: Runner | None = None
    projection_pool: ConnectionPool | None = None
    try:
        _initial_sequence, samples = prepare(args.dsn, args.workspace)
        agent_samples = longitudinal_samples(args.dsn, args.workspace)
        runner = Runner(
            args.dsn,
            args.workspace,
            samples,
            args.seed,
            args.repository_pool_size,
        )
        projection_pool = ConnectionPool(
            args.dsn,
            min_size=min(4, args.concurrency),
            max_size=max(4, min(args.concurrency, 32)),
            timeout=30,
            open=True,
        )
        coordinator = EvidenceSwarmCoordinator(
            [
                AuthoritySpecialist(runner.repository),
                HistoricalSpecialist(runner.repository),
                ProjectionSpecialist(projection_pool),
                ProvenanceSpecialist(runner.repository),
            ],
            max_workers=4,
            timeout_seconds=10,
        )
        before = snapshot(args.dsn, args.workspace)
        dataset = dataset_metadata(args.dsn, args.workspace)
        with start_span(
            "okx.multi_agent_memory.run",
            metadata={
                "workspace_id": args.workspace,
                "seocho.benchmark.operations": args.operations,
                "seocho.benchmark.concurrency": args.concurrency,
                "traffic.type": "evaluation",
            },
            tags=["okx", "blockchain", "long-term-memory", "multi-agent"],
        ) as root_span:
            trace_id = str(getattr(root_span, "trace_id", ""))
            phase = run_mixed_phase(
                runner=runner,
                coordinator=coordinator,
                workspace=args.workspace,
                samples=agent_samples,
                operations=args.operations,
                concurrency=args.concurrency,
                seed=args.seed,
            )
            projection_recovery = drain_all(args.dsn, args.workspace)
            mara = run_mara_samples(
                coordinator=coordinator,
                workspace=args.workspace,
                samples=agent_samples,
                count=args.llm_cases,
                model=args.model,
            )
            after = projection_recovery["snapshot"]
            root_span.set_output(
                {
                    "unexpected_errors": phase["unexpected_errors"],
                    "agent_queries": phase["agent_summary"]["queries"],
                    "multi_agent_queries": phase["agent_summary"][
                        "multi_agent_queries"
                    ],
                    "projection_lag_events": after["projection_lag_events"],
                    "mara_status": mara["status"],
                }
            )
        passed = bool(
            phase["unexpected_errors"] == 0
            and phase["agent_summary"]["queries"] > 0
            and phase["agent_summary"]["multi_agent_queries"] > 0
            and phase["agent_summary"]["protected_filtered"] > 0
            and after["authoritative_integrity"]
            and after["sequence_integrity"]
            and after["projection_lag_events"] == 0
            and (args.llm_cases == 0 or mara["status"] == "complete")
        )
        report = {
            "schema_version": "seocho.okx-multi-agent-memory-live.v1",
            "run_id": os.environ["OTEL_SERVICE_INSTANCE_ID"],
            "trace_id": trace_id,
            "source": "live-postgresql-production-repository-and-sdcr-evidence-swarm",
            "dataset": dataset,
            "agent_query_cohort": {
                "requirement": "memory_revision_count>=2",
                "sample_size": len(agent_samples),
            },
            "workload": {
                "memory_share": MEMORY_WEIGHT,
                "memory_mix": MIX,
                "agent_mix": AGENT_MIX,
                "phase": phase,
            },
            "before": before,
            "after": after,
            "projection_recovery": {
                key: value
                for key, value in projection_recovery.items()
                if key != "snapshot"
            },
            "mara": mara,
            "telemetry": {
                "otlp_endpoint": args.otlp_grpc,
                "service_name": os.environ["OTEL_SERVICE_NAME"],
            },
            "limitations": [
                "The populated million-revision workspace is exchange-shaped synthetic data, not private OKX customer data.",
                "Graph projection consistency uses the live PostgreSQL shadow consumer; DozerDB traversal is validated by separate E2E scenarios.",
                "The fixed-count runner is closed-loop rather than an open-loop arrival process.",
            ],
            "passed": passed,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if passed else 1)
    finally:
        if projection_pool is not None:
            projection_pool.close()
        if runner is not None:
            runner.repository.close()
        flush_tracing()
        disable_tracing()
        shutdown_metrics()


if __name__ == "__main__":
    main()
