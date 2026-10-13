"""Run identical Graph-RAG cases across A–D orchestration modes on MARA/DozerDB."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seocho.agent.collaboration import MODES, CollaborationExperiment, Limits
from seocho.eval.collaboration import RECIPE_PATHS, RecipeRetriever, score_evidence
from seocho.store.graph import Neo4jGraphStore


SEED_COMPANY = "MERGE (c:CollabCompany {id: $id, _workspace_id: $workspace_id})"
SEED_INCIDENT = """
MATCH (c:CollabCompany {id: $company, _workspace_id: $workspace_id})
MERGE (i:CollabIncident {id: $id, _workspace_id: $workspace_id})
SET i.summary = $summary, i.date = $date, i.severity = $severity, i.source = $source
MERGE (c)-[:REPORTED {_workspace_id: $workspace_id}]->(i)
"""
SEED_ATTESTATION = """
MATCH (i:CollabIncident {id: $incident, _workspace_id: $workspace_id})
MERGE (k:CollabControl {id: $control, _workspace_id: $workspace_id})
MERGE (a:CollabAttestation {id: $id, _workspace_id: $workspace_id})
SET a.status = $status, a.date = $date, a.source = $source
MERGE (i)-[:MITIGATED_BY {_workspace_id: $workspace_id}]->(k)
MERGE (k)-[:HAS_EVIDENCE {_workspace_id: $workspace_id}]->(a)
"""


def validate_workload(data: dict[str, Any]) -> None:
    companies = set(data["companies"])
    incidents = {r["id"] for r in data["incidents"]}
    evidence = incidents | {r["id"] for r in data["attestations"]}
    if len(evidence) != len(data["incidents"]) + len(data["attestations"]):
        raise ValueError("Duplicate evidence IDs")
    if any(r["company"] not in companies for r in data["incidents"]):
        raise ValueError("Unknown incident company")
    if any(r["incident"] not in incidents for r in data["attestations"]):
        raise ValueError("Unknown attestation incident")
    ids: set[str] = set()
    for case in data["cases"]:
        if case["id"] in ids or not case["required_slots"] or not case["question"].strip():
            raise ValueError("Invalid case")
        ids.add(case["id"])
        if not case["anchors"] or not set(case["anchors"]) <= companies:
            raise ValueError("Unknown case anchor")
        if not set(case["expected_refs"]) <= evidence:
            raise ValueError("Unknown gold evidence")


def seed(store: Any, data: dict[str, Any], workspace: str, database: str) -> None:
    for company in data["companies"]:
        store.execute_write(SEED_COMPANY, params={"id": company, "workspace_id": workspace}, database=database)
    for row in data["incidents"]:
        store.execute_write(SEED_INCIDENT, params={**row, "workspace_id": workspace}, database=database)
    for row in data["attestations"]:
        store.execute_write(SEED_ATTESTATION, params={**row, "workspace_id": workspace}, database=database)


async def run(args: argparse.Namespace, data: dict[str, Any]) -> int:
    from seocho.recorder.adapters.openai_agents import recorded_client
    from uuid import uuid4

    if not args.recorder_url:
        raise ValueError("Live experiments require --recorder-url; direct provider bypass is disabled")

    required = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise ValueError("Missing environment: " + ", ".join(missing))
    store = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    model, client = recorded_client(recorder_url=args.recorder_url, model=args.model,
        workspace_id=args.workspace_id, run_id=uuid4().hex, tier='T1_RETRIEVAL',
        template_version='graphrag-collaboration-v1', timeout=args.timeout)
    reports = []
    try:
        retriever = RecipeRetriever(store, workspace_id=args.workspace_id, database=args.database)
        if args.seed:
            seed(store, data, args.workspace_id, args.database)
        # Live fixture gate: never silently score an empty/stale/contaminated graph.
        expected_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for company in data["companies"]:
            inc = [r for r in data["incidents"] if r["company"] == company]
            expected_rows[("incidents", company)] = [
                {"evidence_id": r["id"], **{k: r[k] for k in ("company", "summary", "date", "severity", "source")}}
                for r in inc]
            expected_rows[("controls", company)] = [
                {"evidence_id": r["id"], "company": company,
                 **{k: r[k] for k in ("incident", "control", "status", "date", "source")}}
                for r in data["attestations"] if r["incident"] in {i["id"] for i in inc}]
        for (recipe, company), expected in expected_rows.items():
            actual = retriever(recipe, company)
            if sorted(actual, key=lambda r: r["evidence_id"]) != sorted(expected, key=lambda r: r["evidence_id"]):
                raise ValueError(f"Fixture mismatch for {recipe}/{company}; use an isolated workspace and --seed")
        experiment = CollaborationExperiment(
            model=model, retrieve=retriever, recipes=RECIPE_PATHS, workspace_id=args.workspace_id,
            json_object_mode=True,
            limits=Limits(max_calls=args.max_calls, max_admission_units=args.admission_units,
                          timeout_seconds=args.timeout, concurrency=args.concurrency,
                          max_output_tokens=args.output_tokens),
        )
        for case in data["cases"]:
            if args.case and case["id"] != args.case:
                continue
            for mode in args.modes:
                recorder_run_id = uuid4().hex
                experiment.model = model.for_run(recorder_run_id, mode)
                result = await experiment.run(question=case["question"], anchors=case["anchors"],
                                              required_slots=case["required_slots"], mode=mode)
                result['recorder_run_id'] = recorder_run_id
                result['recording_attempts'] = [a for a in model.attempts if a['run_id'] == recorder_run_id]
                result.update(case_id=case["id"], category=case["category"], model=args.model,
                              scores=score_evidence(result, case["expected_refs"]))
                reports.append(result)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps({"workload_id": data["workload_id"],
                                                  "service_context": args.service_context,
                                                  "reports": reports}, ensure_ascii=False, indent=2))
                print(f"{case['id']}/{mode}: {result['status']}; calls={result['budget']['model_calls']}", flush=True)
        print(f"Report: {args.output}")
        return 0 if all(r["status"] == "completed" for r in reports) else 1
    finally:
        await client.close()
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, default=Path(__file__).with_name("workload.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed", action="store_true", help="MERGE fixture into isolated experiment workspace")
    parser.add_argument("--workspace-id", default="collaboration-lab-v1")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--recorder-url", default=os.getenv("SEOCHO_RECORDER_URL"))
    parser.add_argument("--model", default="MiniMax-M2.5")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--case")
    parser.add_argument("--output-tokens", type=int, default=4096)
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--admission-units", type=int, default=240_000)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--service-context", default="unspecified: record DozerDB/provider versions, hardware, limits, warmup")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    parser.add_argument("--output", type=Path, default=Path("outputs/collaboration") / f"{stamp}.json")
    args = parser.parse_args()
    data = json.loads(args.workload.read_text())
    validate_workload(data)
    Limits(max_calls=args.max_calls, max_admission_units=args.admission_units,
           timeout_seconds=args.timeout, concurrency=args.concurrency, max_output_tokens=args.output_tokens)
    if args.case and args.case not in {c["id"] for c in data["cases"]}:
        parser.error("Unknown case")
    if args.dry_run:
        print(f"Validated {data['workload_id']}: {len(data['cases'])} cases, modes={args.modes}; no service calls")
        return 0
    try:
        return asyncio.run(run(args, data))
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
