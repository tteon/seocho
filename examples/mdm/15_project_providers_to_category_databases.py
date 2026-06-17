#!/usr/bin/env python3
"""Project provider/model databases into category-centric databases.

Input topology:
  provider database -> one MARA model/provider owns all categories.

Output topology:
  category database -> all provider/model evidence for that category, with
  provider/model/prompt/ontology provenance preserved as graph properties.

This is a zero-LLM projection over existing graph data.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)

from examples.mdm.agents.provider_agent import workspace_for  # noqa: E402
from examples.mdm.lib import federation  # noqa: E402

BATCH = 500


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _load_index_artifact(run_prefix: str) -> dict[str, Any]:
    path = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / run_prefix / "index_aggregate.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_scenario_artifact(run_prefix: str) -> dict[str, Any]:
    path = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / run_prefix / "scenario_gate_aggregate.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _find_scenario(artifact: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    for scenario in artifact.get("scenarios", []):
        if scenario.get("scenario_id") == scenario_id:
            return scenario
    raise ValueError(f"scenario not found in aggregate: {scenario_id}")


def _load_categories(path: Path) -> dict[str, dict[str, str]]:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload["categories"]


def _load_provider_instances(path: Path) -> dict[str, federation.Instance]:
    return {inst.dept: inst for inst in federation.load_instances(path)}


def _read_provider_case(provider: federation.Instance, case: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    from neo4j import GraphDatabase

    ws = case.get("workspace_id") or workspace_for(provider.dept, case["case_id"])
    driver = GraphDatabase.driver(provider.uri, auth=_auth())
    try:
        with driver.session(database=provider.database) as session:
            nodes = session.run(
                "MATCH (n {_workspace_id: $workspace_id}) "
                "RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props",
                workspace_id=ws,
            ).data()
            rels = session.run(
                "MATCH (a {_workspace_id: $workspace_id})-[r]->(b {_workspace_id: $workspace_id}) "
                "RETURN elementId(a) AS src, elementId(b) AS dst, type(r) AS type, "
                "properties(r) AS props",
                workspace_id=ws,
            ).data()
    finally:
        driver.close()
    return nodes, rels


def _projection_props(
    props: dict[str, Any],
    *,
    source_eid: str,
    provider: federation.Instance,
    case: dict[str, Any],
    index_meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        **props,
        "category": case["category"],
        "case_id": case["case_id"],
        "provider_id": provider.dept,
        "model": provider.model,
        "provider_database": provider.database,
        "provider_uri": provider.uri,
        "source_provider_eid": source_eid,
        "prompt_id": index_meta.get("prompt_id", ""),
        "prompt_hash": index_meta.get("prompt_hash", ""),
        "ontology_hash": index_meta.get("ontology_hash", ""),
        "ontology_modules": ",".join(index_meta.get("ontology_modules", [])),
        "source_run_prefix": index_meta.get("source_run_prefix", ""),
        "source_scenario_id": index_meta.get("source_scenario_id", ""),
        "fedcat_topology": "category_database",
    }


def _baseline_projection_inputs(index_meta: dict[str, Any], providers: dict[str, federation.Instance]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = [
        {
            "case_id": row["case_id"],
            "category": row["category"],
            "slice": row["slice"],
        }
        for row in index_meta["results"]
        if row["provider_id"] == next(iter(providers))
    ]
    return index_meta, cases


def _scenario_projection_inputs(
    *,
    scenario_run_prefix: str,
    scenario_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifact = _load_scenario_artifact(scenario_run_prefix)
    scenario = _find_scenario(artifact, scenario_id)
    prompt = scenario.get("prompt", {})
    ontology = scenario.get("ontology", {})
    results = [
        row
        for row in scenario.get("results", [])
        if row.get("provider_id") and row.get("case_id")
    ]
    if not results:
        raise ValueError(f"scenario has no projection records: {scenario_id}")
    index_meta = {
        "prompt_id": prompt.get("prompt_id", ""),
        "prompt_hash": results[0].get("prompt_hash", ""),
        "ontology_modules": ontology.get("modules", []),
        "ontology_hash": results[0].get("ontology_hash", ""),
        "source_run_prefix": scenario_run_prefix,
        "source_scenario_id": scenario_id,
    }
    cases = [
        {
            "case_id": row["case_id"],
            "category": row["category"],
            "slice": row.get("slice", ""),
            "provider_id": row["provider_id"],
            "workspace_id": row.get("workspace_id"),
            "error": row.get("error", ""),
        }
        for row in results
    ]
    return index_meta, cases


def _write_category_database(
    *,
    category_spec: dict[str, str],
    rows_by_provider_case: list[tuple[federation.Instance, dict[str, Any], list[dict], list[dict]]],
    index_meta: dict[str, Any],
) -> dict[str, Any]:
    from neo4j import GraphDatabase
    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry

    database = category_spec["database"]
    uri = category_spec["uri"]
    store = Neo4jGraphStore(uri, *_auth())
    driver = GraphDatabase.driver(uri, auth=_auth())
    t0 = time.perf_counter()
    nodes_written = 0
    rels_written = 0
    try:
        db_registry.register(database)
        store.ensure_database(database, wait_online=True)
        with driver.session(database=database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            for provider, case, nodes, _rels in rows_by_provider_case:
                for idx in range(0, len(nodes), BATCH):
                    batch = [
                        {
                            "labels": node["labels"],
                            "props": _projection_props(
                                node["props"] or {},
                                source_eid=node["eid"],
                                provider=provider,
                                case=case,
                                index_meta=index_meta,
                            ),
                        }
                        for node in nodes[idx : idx + BATCH]
                    ]
                    session.run(
                        "UNWIND $rows AS row "
                        "CALL apoc.create.node(row.labels, row.props) YIELD node "
                        "RETURN count(node)",
                        rows=batch,
                    ).consume()
                    nodes_written += len(batch)
            session.run(
                "CREATE INDEX source_provider_eid_idx IF NOT EXISTS "
                "FOR (n:LegalEntity) ON (n.source_provider_eid)"
            ).consume()
            session.run(
                "CREATE INDEX fedcat_provider_idx IF NOT EXISTS "
                "FOR (n:LegalEntity) ON (n.provider_id)"
            ).consume()
            for provider, case, _nodes, rels in rows_by_provider_case:
                for idx in range(0, len(rels), BATCH):
                    batch = [
                        {
                            "src": rel["src"],
                            "dst": rel["dst"],
                            "type": rel["type"],
                            "props": _projection_props(
                                rel["props"] or {},
                                source_eid=f"{rel['src']}->{rel['dst']}",
                                provider=provider,
                                case=case,
                                index_meta=index_meta,
                            ),
                        }
                        for rel in rels[idx : idx + BATCH]
                    ]
                    session.run(
                        "UNWIND $rows AS row "
                        "MATCH (a {source_provider_eid: row.src, provider_id: row.props.provider_id}), "
                        "      (b {source_provider_eid: row.dst, provider_id: row.props.provider_id}) "
                        "CALL apoc.create.relationship(a, row.type, row.props, b) YIELD rel "
                        "RETURN count(rel)",
                        rows=batch,
                    ).consume()
                    rels_written += len(batch)
    finally:
        driver.close()
        store.close()

    return {
        "category": category_spec["category"],
        "database": database,
        "uri": uri,
        "nodes": nodes_written,
        "rels": rels_written,
        "seconds": round(time.perf_counter() - t0, 2),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-prefix", default="fedcat-single-dbms-v1")
    parser.add_argument("--index-run-prefix", default="fedcat-v1")
    parser.add_argument("--scenario-run-prefix", default="",
                        help="read scenario_gate_aggregate.json from this run instead of index_aggregate.json")
    parser.add_argument("--scenario-id", default="",
                        help="scenario id to project when --scenario-run-prefix is set")
    parser.add_argument("--providers-config", default=str(MDM_ROOT / "config" / "provider_databases.yaml"))
    parser.add_argument("--categories-config", default=str(MDM_ROOT / "config" / "category_databases.yaml"))
    args = parser.parse_args()

    providers = _load_provider_instances(Path(args.providers_config))
    categories = _load_categories(Path(args.categories_config))
    if args.scenario_run_prefix:
        if not args.scenario_id:
            raise ValueError("--scenario-id is required with --scenario-run-prefix")
        index_meta, cases = _scenario_projection_inputs(
            scenario_run_prefix=args.scenario_run_prefix,
            scenario_id=args.scenario_id,
        )
    else:
        index_meta, cases = _baseline_projection_inputs(
            _load_index_artifact(args.index_run_prefix),
            providers,
        )
    by_category: dict[str, list[tuple[federation.Instance, dict[str, Any], list[dict], list[dict]]]] = defaultdict(list)
    for case in cases:
        if case.get("provider_id"):
            provider = providers[case["provider_id"]]
            nodes, rels = _read_provider_case(provider, case)
            by_category[case["category"]].append((provider, case, nodes, rels))
            continue
        for provider in providers.values():
            nodes, rels = _read_provider_case(provider, case)
            by_category[case["category"]].append((provider, case, nodes, rels))

    results = []
    for slug, spec in categories.items():
        category = spec["category"]
        rec = _write_category_database(
            category_spec=spec,
            rows_by_provider_case=by_category.get(category, []),
            index_meta=index_meta,
        )
        rec["slug"] = slug
        results.append(rec)
        print(
            f"  [{category:<18}] {rec['database']:<17} "
            f"nodes={rec['nodes']:<4} rels={rec['rels']:<5} {rec['seconds']}s"
        )

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / args.run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix,
        "index_run_prefix": args.index_run_prefix,
        "scenario_run_prefix": args.scenario_run_prefix,
        "scenario_id": args.scenario_id,
        "providers_config": args.providers_config,
        "categories_config": args.categories_config,
        "prompt_id": index_meta.get("prompt_id", ""),
        "prompt_hash": index_meta.get("prompt_hash", ""),
        "ontology_modules": index_meta.get("ontology_modules", []),
        "ontology_hash": index_meta.get("ontology_hash", ""),
        "results": results,
    }
    path = out_dir / "category_projection_artifact.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"== wrote {path.relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
