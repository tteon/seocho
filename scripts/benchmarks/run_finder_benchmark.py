#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seocho import NodeDef, Ontology, P, RelDef, Seocho  # noqa: E402
from seocho.benchmarking import (  # noqa: E402
    FinDERBenchmarkRecord,
    classify_finder_scenario,
    compare_answers,
    filter_finder_cases,
    load_finder_cases,
    run_finder_benchmark,
    summarize_finder_records,
)
from seocho.tracing import (  # noqa: E402
    configure_tracing_from_env,
    current_backend_names,
    flush_tracing,
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _default_dataset_path() -> Path:
    return ROOT / "examples" / "datasets" / "finder_sample.json"


def _output_dir() -> Path:
    path = ROOT / "outputs" / "evaluation" / "finder_benchmark"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_graph_path_for_run(
    workspace_id: str,
    *,
    base_dir: Path | None = None,
    fresh: bool = False,
) -> str:
    root = base_dir or (ROOT / ".seocho" / "benchmarks" / "local")
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", str(workspace_id).lower()).strip("-") or "finder-local"
    path = root / f"{slug}.lbug"
    if fresh and path.exists():
        path.unlink()
    return str(path)


def _build_finder_ontology() -> Ontology:
    return Ontology(
        name="finder_benchmark",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
            "Person": NodeDef(properties={"name": P(str, unique=True), "title": P(str)}),
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "year": P(str)}),
            "Risk": NodeDef(properties={"name": P(str, unique=True), "category": P(str)}),
            "LegalIssue": NodeDef(properties={"name": P(str, unique=True), "status": P(str)}),
            "AccountingStandard": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            "EMPLOYS": RelDef(source="Company", target="Person"),
            "FACES": RelDef(source="Company", target="Risk"),
            "INVOLVED_IN": RelDef(source="Company", target="LegalIssue"),
            "USES_STANDARD": RelDef(source="Company", target="AccountingStandard"),
        },
    )


def _build_local_client(args: argparse.Namespace) -> Seocho:
    kwargs = {
        "llm": args.model,
        "workspace_id": args.workspace_id,
    }
    if args.graph:
        kwargs.update(
            {
                "graph": args.graph,
                "neo4j_user": args.neo4j_user,
                "neo4j_password": args.neo4j_password,
            }
        )
    else:
        kwargs["graph"] = args.local_graph_path
    return Seocho.local(
        _build_finder_ontology(),
        **kwargs,
    )


def _write_summary(payload: dict) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _output_dir() / f"finder_benchmark_{timestamp}.json"
    target.write_text(json.dumps(payload, indent=2))
    return target


def _benchmark_setup_payload(args: argparse.Namespace, *, tracing_configured: bool) -> dict:
    active_trace_backends = current_backend_names()
    return {
        "provider": "openai",
        "model": args.model,
        "trace_backend_env": os.getenv("SEOCHO_TRACE_BACKEND", "none"),
        "active_trace_backends": active_trace_backends,
        "tracing_configured": tracing_configured,
        "opik_project": os.getenv("OPIK_PROJECT_NAME", ""),
        "opik_workspace": os.getenv("OPIK_WORKSPACE", ""),
    }


def _scenario_counts(cases: list) -> dict[str, int]:
    counts = {"beginner": 0, "advanced": 0}
    for case in cases:
        counts[classify_finder_scenario(case)] += 1
    return counts


def _request_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
    query: dict | None = None,
    timeout: float = 120.0,
) -> tuple[int, object]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return int(exc.code), parsed
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _extract_answer(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("response", "assistant_message", "answer"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    runtime_payload = payload.get("runtime_payload")
    if isinstance(runtime_payload, dict):
        for key in ("response", "assistant_message", "answer"):
            value = runtime_payload.get(key)
            if isinstance(value, str):
                return value
    return ""


def _extract_reasoning_cycle(payload: object) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        return "", []
    report = payload.get("reasoning_cycle")
    if not isinstance(report, dict):
        runtime_payload = payload.get("runtime_payload")
        if isinstance(runtime_payload, dict):
            report = runtime_payload.get("reasoning_cycle")
    if not isinstance(report, dict):
        return "", []
    status = str(report.get("status", "")).strip()
    sources = [
        str(item.get("source", "")).strip()
        for item in report.get("observed_anomalies", [])
        if isinstance(item, dict) and str(item.get("source", "")).strip()
    ]
    return status, sources


def _runtime_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    runtime_payload = payload.get("runtime_payload")
    if isinstance(runtime_payload, dict):
        return runtime_payload
    return payload


def _extract_trace_steps(payload: object) -> list[dict]:
    steps: list[dict] = []
    if isinstance(payload, dict) and isinstance(payload.get("trace_steps"), list):
        steps.extend(item for item in payload["trace_steps"] if isinstance(item, dict))
    runtime_payload = _runtime_payload(payload)
    if runtime_payload is not payload and isinstance(runtime_payload.get("trace_steps"), list):
        steps.extend(item for item in runtime_payload["trace_steps"] if isinstance(item, dict))
    return steps


def _extract_evidence_bundle(payload: object) -> dict:
    runtime_payload = _runtime_payload(payload)
    if isinstance(runtime_payload.get("evidence_bundle"), dict):
        return runtime_payload["evidence_bundle"]
    semantic_context = runtime_payload.get("semantic_context")
    if isinstance(semantic_context, dict) and isinstance(semantic_context.get("evidence_bundle_preview"), dict):
        return semantic_context["evidence_bundle_preview"]
    return {}


def _evidence_bundle_size(bundle: dict) -> int:
    selected = bundle.get("selected_triples", [])
    slot_fills = bundle.get("slot_fills", {})
    grounded = bundle.get("grounded_slots", [])
    return (
        (len(selected) if isinstance(selected, list) else 0)
        + (len(slot_fills) if isinstance(slot_fills, dict) else 0)
        + (len(grounded) if isinstance(grounded, list) else 0)
    )


def _extract_token_usage(trace_steps: list[dict]) -> dict:
    for step in reversed(trace_steps):
        metadata = step.get("metadata")
        if not isinstance(metadata, dict):
            continue
        usage = metadata.get("usage")
        if isinstance(usage, dict):
            return dict(usage)
    return {}


def _extract_agent_metrics(payload: object) -> dict:
    runtime_payload = _runtime_payload(payload)
    trace_steps = _extract_trace_steps(payload)
    support_assessment = runtime_payload.get("support_assessment")
    if not isinstance(support_assessment, dict):
        support_assessment = {}
    evidence_bundle = _extract_evidence_bundle(payload)
    missing_slots = evidence_bundle.get("missing_slots") or support_assessment.get("missing_slots") or []
    if not isinstance(missing_slots, list):
        missing_slots = []

    tool_call_count = 0
    reasoning_attempt_count = 0
    semantic_reused = False
    for step in trace_steps:
        step_type = str(step.get("type", "")).strip()
        if step_type in {"TOOL_CALL", "DETERMINISTIC_PREFLIGHT", "DETERMINISTIC_FALLBACK"}:
            tool_call_count += 1
        if step_type in {"DETERMINISTIC_PREFLIGHT", "DETERMINISTIC_FALLBACK", "SYNTHESIS_BYPASSED"}:
            semantic_reused = True
        metadata = step.get("metadata")
        if isinstance(metadata, dict):
            tool_names = metadata.get("tool_names")
            if isinstance(tool_names, list) and step_type == "TOOL_CALL":
                tool_call_count += max(0, len(tool_names) - 1)
            repair_trace = metadata.get("tool_calls")
            if isinstance(repair_trace, list):
                tool_call_count += len(repair_trace)
            reasoning_attempt_count = max(
                reasoning_attempt_count,
                int(metadata.get("reasoning_attempts", 0) or 0),
            )

    lpg_result = runtime_payload.get("lpg_result")
    if isinstance(lpg_result, dict):
        reasoning = lpg_result.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning_attempt_count = max(
                reasoning_attempt_count,
                int(reasoning.get("attempt_count", 0) or 0),
            )

    debate_results = runtime_payload.get("debate_results")
    if isinstance(debate_results, list):
        semantic_reused = semantic_reused or any(
            bool(item.get("semantic_reused")) for item in debate_results if isinstance(item, dict)
        )

    return {
        "route": str(runtime_payload.get("route", "") or ""),
        "support_status": str(support_assessment.get("status", "") or ""),
        "support_coverage": float(support_assessment.get("coverage", 0.0) or 0.0),
        "missing_slots": [str(slot) for slot in missing_slots if str(slot).strip()],
        "evidence_bundle_size": _evidence_bundle_size(evidence_bundle),
        "trace_step_count": len(trace_steps),
        "tool_call_count": tool_call_count,
        "reasoning_attempt_count": reasoning_attempt_count,
        "semantic_reused": semantic_reused,
        "debate_state": str(runtime_payload.get("debate_state", "") or ""),
        "token_usage": _extract_token_usage(trace_steps),
    }


def _default_reasoning_cycle_payload() -> dict:
    return {
        "enabled": True,
        "anomaly_sources": [
            "unsupported_answer",
            "ontology_mismatch",
            "query_execution_failed_or_contract_error",
        ],
    }


def _remote_setup(args: argparse.Namespace, cases: list) -> dict:
    records = [
        {
            "id": case.case_id,
            "content": case.text,
            "category": case.category,
            "source_type": "text",
            "metadata": {
                "benchmark_case_id": case.case_id,
                "question": case.question,
                "expected_answer": case.expected_answer,
                "reasoning_type": case.reasoning_type,
            },
        }
        for case in cases
    ]

    started = time.perf_counter()
    ingest_status, ingest_payload = _request_json(
        base_url=args.base_url,
        method="POST",
        path="/platform/ingest/raw",
        payload={
            "workspace_id": args.workspace_id,
            "target_database": args.database,
            "records": records,
            "enable_rule_constraints": True,
            "create_database_if_missing": True,
            "semantic_artifact_policy": "auto",
        },
        timeout=args.timeout,
    )
    ingest_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)

    started = time.perf_counter()
    fulltext_status, fulltext_payload = _request_json(
        base_url=args.base_url,
        method="POST",
        path="/indexes/fulltext/ensure",
        payload={
            "workspace_id": args.workspace_id,
            "databases": [args.database],
            "index_name": "entity_fulltext",
            "create_if_missing": True,
        },
        timeout=args.timeout,
    )
    fulltext_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)

    return {
        "ingest_status_code": ingest_status,
        "ingest_latency_ms": ingest_latency_ms,
        "ingest_payload": ingest_payload,
        "fulltext_status_code": fulltext_status,
        "fulltext_latency_ms": fulltext_latency_ms,
        "fulltext_payload": fulltext_payload,
    }


def _run_remote_endpoint_benchmark(
    args: argparse.Namespace,
    cases: list,
    *,
    mode: str,
    path: str,
    payload_factory,
    setup_latency_ms: float,
) -> dict:
    records: list[FinDERBenchmarkRecord] = []
    amortized_add_latency_ms = round(setup_latency_ms / max(1, len(cases)), 2)
    for case in cases:
        answer = ""
        error = ""
        exact = False
        contains = False
        reasoning_cycle_status = ""
        reasoning_cycle_sources: list[str] = []
        agent_metrics = {
            "route": "",
            "support_status": "",
            "support_coverage": 0.0,
            "missing_slots": [],
            "evidence_bundle_size": 0,
            "trace_step_count": 0,
            "tool_call_count": 0,
            "reasoning_attempt_count": 0,
            "semantic_reused": False,
            "debate_state": "",
            "token_usage": {},
        }
        started = time.perf_counter()
        try:
            status_code, payload = _request_json(
                base_url=args.base_url,
                method="POST",
                path=path,
                payload=payload_factory(case),
                timeout=args.timeout,
            )
            ask_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if 200 <= status_code < 300:
                answer = _extract_answer(payload)
                reasoning_cycle_status, reasoning_cycle_sources = _extract_reasoning_cycle(payload)
                agent_metrics = _extract_agent_metrics(payload)
                exact, contains = compare_answers(case.expected_answer, answer)
            else:
                detail = payload.get("detail") if isinstance(payload, dict) else ""
                error = str(detail or payload)
        except Exception as exc:  # pragma: no cover
            ask_latency_ms = 0.0
            error = str(exc)

        records.append(
            FinDERBenchmarkRecord(
                case_id=case.case_id,
                category=case.category,
                question=case.question,
                add_latency_ms=amortized_add_latency_ms,
                ask_latency_ms=round(ask_latency_ms, 2),
                answer=answer,
                expected_answer=case.expected_answer,
                exact_match=exact,
                contains_match=contains,
                nodes_created=0,
                relationships_created=0,
                reasoning_cycle_status=reasoning_cycle_status,
                reasoning_cycle_sources=reasoning_cycle_sources,
                route=str(agent_metrics["route"]),
                support_status=str(agent_metrics["support_status"]),
                support_coverage=float(agent_metrics["support_coverage"]),
                missing_slots=list(agent_metrics["missing_slots"]),
                evidence_bundle_size=int(agent_metrics["evidence_bundle_size"]),
                trace_step_count=int(agent_metrics["trace_step_count"]),
                tool_call_count=int(agent_metrics["tool_call_count"]),
                reasoning_attempt_count=int(agent_metrics["reasoning_attempt_count"]),
                semantic_reused=bool(agent_metrics["semantic_reused"]),
                debate_state=str(agent_metrics["debate_state"]),
                token_usage=dict(agent_metrics["token_usage"]),
                error=error,
            )
        )
    return summarize_finder_records(
        mode=mode,
        dataset=str(args.dataset),
        records=records,
    ).to_dict()


def main() -> int:
    _load_dotenv(ROOT / ".env")
    tracing_configured = configure_tracing_from_env()
    parser = argparse.ArgumentParser(description="Run the SEOCHO FinDER benchmark.")
    parser.add_argument("--mode", choices=("local", "remote", "both"), default="local")
    parser.add_argument(
        "--dataset",
        default=str(_default_dataset_path()),
        help="Path to a FinDER-format JSON dataset. This repo does not ship benchmark evidence.",
    )
    parser.add_argument("--scenario", choices=("all", "beginner", "advanced"), default="all")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--base-url", default=os.getenv("SEOCHO_BASE_URL", "http://localhost:8001"))
    parser.add_argument(
        "--graph",
        default=os.getenv("NEO4J_URI", "") if os.getenv("FINDER_USE_BOLT") else "",
        help="Optional Bolt URI. Leave empty to use embedded LadybugDB for Seocho.local().",
    )
    parser.add_argument(
        "--local-graph-path",
        default="",
        help="Optional Ladybug file path for local mode. Defaults to an isolated per-workspace benchmark file.",
    )
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", "password"))
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--workspace-id", default=f"finder-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--reasoning-mode", action="store_true")
    parser.add_argument("--repair-budget", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=float(os.getenv("FINDER_RUNTIME_TIMEOUT", "180")))
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(
            f"FinDER dataset not found: {dataset_path}. Pass --dataset /path/to/finder_sample.json."
        )
    if not args.graph:
        args.local_graph_path = args.local_graph_path or _local_graph_path_for_run(
            args.workspace_id,
            fresh=True,
        )

    all_cases = load_finder_cases(dataset_path)
    cases = filter_finder_cases(all_cases, args.scenario)
    if not cases:
        raise SystemExit(f"No FinDER cases matched scenario={args.scenario}.")

    summaries = []

    if args.mode in {"local", "both"}:
        local_client = _build_local_client(args)
        try:
            summaries.append(
                run_finder_benchmark(
                    client=local_client,
                    cases=cases,
                    mode="local",
                    dataset=str(dataset_path),
                    database=args.database,
                ).to_dict()
            )
        finally:
            local_client.close()

    if args.mode in {"remote", "both"}:
        runtime_setup = _remote_setup(args, cases)
        output_runtime_setup = runtime_setup
        summaries.append(
            _run_remote_endpoint_benchmark(
                args,
                cases,
                mode="remote-semantic",
                path="/run_agent_semantic",
                setup_latency_ms=float(runtime_setup["ingest_latency_ms"]),
                payload_factory=lambda case: {
                    "query": case.question,
                    "workspace_id": args.workspace_id,
                    "user_id": "finder_runtime_benchmark",
                    "databases": [args.database],
                    "reasoning_mode": args.reasoning_mode,
                    "repair_budget": args.repair_budget if args.reasoning_mode else 0,
                    "reasoning_cycle": _default_reasoning_cycle_payload(),
                },
            )
        )
        summaries.append(
            _run_remote_endpoint_benchmark(
                args,
                cases,
                mode="remote-debate",
                path="/run_debate",
                setup_latency_ms=float(runtime_setup["ingest_latency_ms"]),
                payload_factory=lambda case: {
                    "query": case.question,
                    "workspace_id": args.workspace_id,
                    "user_id": "finder_runtime_benchmark",
                    "graph_ids": [args.database],
                    "reasoning_cycle": _default_reasoning_cycle_payload(),
                },
            )
        )
        summaries.append(
            _run_remote_endpoint_benchmark(
                args,
                cases,
                mode="remote-platform-semantic",
                path="/platform/chat/send",
                setup_latency_ms=float(runtime_setup["ingest_latency_ms"]),
                payload_factory=lambda case: {
                    "session_id": f"{args.workspace_id}-platform-{case.case_id}",
                    "message": case.question,
                    "mode": "semantic",
                    "workspace_id": args.workspace_id,
                    "user_id": "finder_runtime_benchmark",
                    "databases": [args.database],
                    "reasoning_cycle": _default_reasoning_cycle_payload(),
                },
            )
        )
    else:
        output_runtime_setup = {}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "benchmark_setup": _benchmark_setup_payload(args, tracing_configured=tracing_configured),
        "database": args.database,
        "workspace_id": args.workspace_id,
        "scenario": args.scenario,
        "scenario_counts": _scenario_counts(all_cases),
        "selected_case_ids": [case.case_id for case in cases],
        "local_graph_path": args.local_graph_path if not args.graph else "",
        "runtime_setup": output_runtime_setup,
        "summaries": summaries,
    }
    path = _write_summary(output)
    flush_tracing()
    print(
        json.dumps(
            {
                "output_path": str(path),
                "modes": [item["mode"] for item in summaries],
                "scenario": args.scenario,
                "selected_case_ids": output["selected_case_ids"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
