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
from seocho.benchmarking import load_finder_cases, run_finder_benchmark  # noqa: E402


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
    return Seocho.local(
        _build_finder_ontology(),
        llm=args.model,
        graph=args.graph,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        workspace_id=args.workspace_id,
    )


def _build_remote_client(args: argparse.Namespace) -> Seocho:
    return Seocho.remote(args.base_url, workspace_id=args.workspace_id)


def _write_summary(payload: dict) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _output_dir() / f"finder_baseline_{timestamp}.json"
    target.write_text(json.dumps(payload, indent=2))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SEOCHO FinDER baseline benchmark.")
    parser.add_argument("--mode", choices=("local", "remote", "both"), default="local")
    parser.add_argument("--dataset", default=str(_default_dataset_path()))
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--base-url", default=os.getenv("SEOCHO_BASE_URL", "http://localhost:8001"))
    parser.add_argument("--graph", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", "password"))
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--workspace-id", default=f"finder-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    args = parser.parse_args()

    cases = load_finder_cases(args.dataset)
    summaries = []

    if args.mode in {"local", "both"}:
        local_client = _build_local_client(args)
        summaries.append(
            run_finder_benchmark(
                client=local_client,
                cases=cases,
                mode="local",
                dataset=str(args.dataset),
                database=args.database,
            ).to_dict()
        )

    if args.mode in {"remote", "both"}:
        remote_client = _build_remote_client(args)
        summaries.append(
            run_finder_benchmark(
                client=remote_client,
                cases=cases,
                mode="remote",
                dataset=str(args.dataset),
                database=args.database,
            ).to_dict()
        )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "database": args.database,
        "workspace_id": args.workspace_id,
        "summaries": summaries,
    }
    path = _write_summary(output)
    print(json.dumps({"output_path": str(path), "modes": [item["mode"] for item in summaries]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
