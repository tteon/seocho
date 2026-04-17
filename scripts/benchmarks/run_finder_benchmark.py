#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
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
    return Seocho.local(
        _build_finder_ontology(),
        **kwargs,
    )


def _build_remote_client(args: argparse.Namespace) -> Seocho:
    return Seocho.remote(args.base_url, workspace_id=args.workspace_id)


def _write_summary(payload: dict) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _output_dir() / f"finder_benchmark_{timestamp}.json"
    target.write_text(json.dumps(payload, indent=2))
    return target


def _scenario_counts(cases: list) -> dict[str, int]:
    counts = {"beginner": 0, "advanced": 0}
    for case in cases:
        counts[classify_finder_scenario(case)] += 1
    return counts


def _run_remote_semantic_benchmark(args: argparse.Namespace, client: Seocho, cases: list) -> dict:
    records: list[FinDERBenchmarkRecord] = []
    for case in cases:
        add_started = datetime.now(timezone.utc)
        answer = ""
        error = ""
        exact = False
        contains = False
        nodes_created = 0
        relationships_created = 0
        try:
            before = datetime.now(timezone.utc)
            memory = client.add(case.text, database=args.database, category=case.category)
            add_latency_ms = (datetime.now(timezone.utc) - before).total_seconds() * 1000.0
            metadata = dict(getattr(memory, "metadata", {}) or {})
            nodes_created = int(metadata.get("nodes_created", 0) or 0)
            relationships_created = int(metadata.get("relationships_created", 0) or 0)

            before = datetime.now(timezone.utc)
            result = client.semantic(
                case.question,
                databases=[args.database],
                reasoning_mode=args.reasoning_mode,
                repair_budget=args.repair_budget,
            )
            ask_latency_ms = (datetime.now(timezone.utc) - before).total_seconds() * 1000.0
            answer = str(result.response)
            exact, contains = compare_answers(case.expected_answer, answer)
        except Exception as exc:  # pragma: no cover
            add_latency_ms = (datetime.now(timezone.utc) - add_started).total_seconds() * 1000.0
            ask_latency_ms = 0.0
            error = str(exc)

        records.append(
            FinDERBenchmarkRecord(
                case_id=case.case_id,
                category=case.category,
                add_latency_ms=round(add_latency_ms, 2),
                ask_latency_ms=round(ask_latency_ms, 2),
                answer=answer,
                expected_answer=case.expected_answer,
                exact_match=exact,
                contains_match=contains,
                nodes_created=nodes_created,
                relationships_created=relationships_created,
                error=error,
            )
        )
    return summarize_finder_records(
        mode="remote-semantic",
        dataset=str(args.dataset),
        records=records,
    ).to_dict()


def main() -> int:
    _load_dotenv(ROOT / ".env")
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
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", "password"))
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--workspace-id", default=f"finder-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--reasoning-mode", action="store_true")
    parser.add_argument("--repair-budget", type=int, default=0)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(
            f"FinDER dataset not found: {dataset_path}. Pass --dataset /path/to/finder_sample.json."
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
        remote_client = _build_remote_client(args)
        try:
            summaries.append(_run_remote_semantic_benchmark(args, remote_client, cases))
        finally:
            remote_client.close()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "database": args.database,
        "workspace_id": args.workspace_id,
        "scenario": args.scenario,
        "scenario_counts": _scenario_counts(all_cases),
        "selected_case_ids": [case.case_id for case in cases],
        "summaries": summaries,
    }
    path = _write_summary(output)
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
