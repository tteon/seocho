#!/usr/bin/env python3
"""Cost-gated prompt/ontology scenario runner for hq-42k.

The full prompt × ontology matrix is expensive: 20 scenarios × 16 cases × 4
providers.  This script runs a small, isolated smoke sample first, computes a
simple extraction census against the current baseline for the same cases, and
marks which scenarios are worth promoting to a full reindex.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT, ROOT / "scripts" / "benchmarks"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)

from examples.finder.lib import bench_common as bc  # noqa: E402
from examples.mdm.agents.provider_agent import workspace_for  # noqa: E402
from examples.mdm.lib import federation  # noqa: E402
from examples.mdm.lib.normalize import is_token_prefix, norm_key, norm_tokens  # noqa: E402

INFRA = set(federation.INFRA_LABELS)
DEFAULT_SCENARIOS = (
    "fibo_strict_entity_first@v1__fibo_medium_current",
    "category_aware_fact_first@v1__fibo_medium_current",
    "duplicate_aware_survivorship@v1__fibo_medium_current",
    "neutral_kg@v1__generic_baseline",
)
DEFAULT_PROVIDERS = ("minimax25", "minimax27")
GENERIC_ENTITY_NAMES = {
    "company",
    "the",
    "specifically",
    "table",
    "note",
    "amounts",
    "registrant",
    "we",
    "our",
}


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _load_indexer():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "idx_providers", MDM_ROOT / "11_index_providers.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load 11_index_providers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_matrix(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_by_id(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["scenario_id"]: row for row in matrix["scenarios"]}


def _prompt_variant_system(prompt_id: str) -> tuple[str, str]:
    system_tmpl, resolved_id, _prompt_file = bc.resolve_extraction_prompt("mara")
    if prompt_id == "neutral_kg@v1":
        return system_tmpl, resolved_id
    additions = {
        "fibo_strict_entity_first@v1": (
            "\n\n## SCENARIO VARIANT: FIBO STRICT ENTITY FIRST\n"
            "- Prefer concrete business entities, issuers, securities, obligations, "
            "segments, products, and counterparties over generic document words.\n"
            "- Suppress generic entities such as 'the', 'company', 'amounts', and "
            "'table' unless they carry a specific business role in the source.\n"
            "- Preserve aliases and legal names when present so cross-provider "
            "SAME_AS clustering can be evaluated."
        ),
        "category_aware_fact_first@v1": (
            "\n\n## SCENARIO VARIANT: CATEGORY AWARE FACT FIRST\n"
            "- For financial, accounting, footnote, and shareholder-return text, "
            "prioritize value-bearing metric nodes with name, value, period, basis, "
            "unit, segment, and source evidence where available.\n"
            "- Keep related entity context, but do not bury numeric facts only in "
            "relationship prose."
        ),
        "duplicate_aware_survivorship@v1": (
            "\n\n## SCENARIO VARIANT: DUPLICATE AWARE SURVIVORSHIP\n"
            "- Extract identifiers, aliases, legal names, ticker-like strings, "
            "period, basis, and source wording needed to decide SAME_AS and "
            "numeric survivorship later.\n"
            "- Preserve conflicting candidate values instead of silently choosing "
            "one value."
        ),
    }
    if prompt_id not in additions:
        raise ValueError(f"unknown prompt variant: {prompt_id}")
    return system_tmpl + additions[prompt_id], prompt_id


def _build_extraction(prompt_id: str, modules: list[str]):
    from examples.finder.datasets.fibo_modules.compose import compose_modules
    from finder_4arm_sample import KGPromptTemplate

    ontology = compose_modules(modules)
    octx = ontology.to_extraction_context()
    onto_hash = bc.short_hash(
        octx.get("entity_types", "") + "\n" + octx.get("relationship_types", "")
    )
    system_tmpl, resolved_prompt_id = _prompt_variant_system(prompt_id)
    prompt_hash = bc.short_hash(system_tmpl)
    return {
        "ontology": ontology,
        "ontology_hash": onto_hash,
        "prompt_id": resolved_prompt_id,
        "prompt_hash": prompt_hash,
        "extraction_tmpl": KGPromptTemplate(
            system=system_tmpl,
            user="Source 10-K text to extract into the graph:\n\n{{text}}",
        ),
    }


def _scenario_workspace(scenario_slug: str, provider_id: str, case_id: str) -> str:
    return f"fedcat-scenario-{scenario_slug}-{provider_id}-{case_id}"


def _pick_cases(cases: list[dict[str, Any]], scenario: dict[str, Any], n_per_scenario: int) -> list[dict[str, Any]]:
    prompt_id = scenario["prompt"]["prompt_id"]
    preferred = {
        "fibo_strict_entity_first@v1": {"Company overview", "Accounting", "Governance", "Legal"},
        "category_aware_fact_first@v1": {"Financials", "Footnotes"},
        "duplicate_aware_survivorship@v1": {"Shareholder return"},
        "neutral_kg@v1": {"Company overview", "Financials"},
    }.get(prompt_id, set())
    selected = [case for case in cases if case["category"] in preferred]
    if len(selected) < n_per_scenario:
        selected.extend(case for case in cases if case not in selected)
    return selected[:n_per_scenario]


def _ensure_provider_databases(instances: list[federation.Instance]) -> None:
    from extraction.config import db_registry
    from seocho.store.graph import Neo4jGraphStore

    seen: set[tuple[str, str]] = set()
    for inst in instances:
        key = (inst.uri, inst.database)
        if key in seen:
            continue
        seen.add(key)
        store = Neo4jGraphStore(inst.uri, *_auth())
        try:
            if inst.database == "neo4j":
                store.query("RETURN 1 AS ok", database="neo4j")
            else:
                db_registry.register(inst.database)
                store.ensure_database(inst.database, wait_online=True)
        finally:
            store.close()


def _extract_one(
    *,
    inst: federation.Instance,
    case: dict[str, Any],
    workspace_id: str,
    ontology,
    extraction_tmpl,
) -> dict[str, Any]:
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    started = time.perf_counter()
    error = ""
    nodes = rels = 0
    client = None
    try:
        graph_store = Neo4jGraphStore(inst.uri, *_auth())
        llm = create_llm_backend(provider="mara", model=inst.model)
        client = Seocho(
            ontology=ontology,
            graph_store=graph_store,
            llm=llm,
            workspace_id=workspace_id,
            extraction_prompt=extraction_tmpl,
        )
        client.default_database = inst.database
        try:
            graph_store.ensure_constraints(ontology, database=inst.database)
        except Exception:
            pass
        for ref in case["references"]:
            client.add(ref, user_id=workspace_id)
        node_rows = graph_store.query(
            "MATCH (n {_workspace_id:$workspace_id}) RETURN count(n) AS count",
            params={"workspace_id": workspace_id},
            database=inst.database,
        )
        rel_rows = graph_store.query(
            "MATCH ({_workspace_id:$workspace_id})-[r]->() RETURN count(r) AS count",
            params={"workspace_id": workspace_id},
            database=inst.database,
        )
        nodes = int(node_rows[0]["count"])
        rels = int(rel_rows[0]["count"])
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return {
        "provider_id": inst.dept,
        "model": inst.model,
        "uri": inst.uri,
        "database": inst.database,
        "case_id": case["case_id"],
        "category": case["category"],
        "workspace_id": workspace_id,
        "nodes_created": nodes,
        "rels_created": rels,
        "latency_s": round(time.perf_counter() - started, 2),
        "error": error,
    }


def _primary_label(labels: list[str]) -> str:
    business = [label for label in labels if label not in INFRA]
    if not business:
        return ""
    for label in ("LegalEntity", "Entity", "Company", "Organization", "Issuer", "Security", "FinancialMetric"):
        if label in business:
            return label
    return sorted(business)[0]


def _read_inventory(instances: list[federation.Instance], workspaces: dict[tuple[str, str], str]) -> tuple[list[dict], list[dict]]:
    from neo4j import GraphDatabase

    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    by_inst: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for (provider_id, case_id), workspace_id in workspaces.items():
        inst = next(i for i in instances if i.dept == provider_id)
        by_inst[(inst.uri, inst.database, provider_id)].append(workspace_id)

    for (uri, database, provider_id), workspace_ids in by_inst.items():
        driver = GraphDatabase.driver(uri, auth=_auth())
        try:
            with driver.session(database=database) as session:
                rows = session.run(
                    """
                    MATCH (n)
                    WHERE n._workspace_id IN $workspace_ids
                      AND n.name IS NOT NULL
                      AND any(label IN labels(n) WHERE NOT label IN $infra)
                    RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props,
                           n._workspace_id AS workspace_id
                    """,
                    workspace_ids=workspace_ids,
                    infra=federation.INFRA_LABELS,
                ).data()
        finally:
            driver.close()
        for row in rows:
            props = row["props"] or {}
            labels = [label for label in (row["labels"] or []) if label not in INFRA]
            name = str(props.get("name") or "")
            case_id = ""
            for (pid, cid), ws in workspaces.items():
                if pid == provider_id and ws == row["workspace_id"]:
                    case_id = cid
                    break
            record = {
                "provider_id": provider_id,
                "case_id": case_id,
                "workspace_id": str(row["workspace_id"]),
                "labels": sorted(labels),
                "primary_label": _primary_label(labels),
                "name": name,
                "normalized_name": norm_key(name),
                "value_raw": str(props.get("value") or ""),
                "period": str(props.get("period") or ""),
                "basis": str(props.get("basis") or ""),
            }
            if props.get("value") is not None:
                facts.append(record)
            else:
                entities.append(record)
    return entities, facts


def _census(entities: list[dict[str, Any]], facts: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        if entity["normalized_name"]:
            by_key[(entity["normalized_name"], entity["primary_label"])].append(entity)
    prefix_pairs = 0
    rows = list(entities)
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i]["primary_label"] != rows[j]["primary_label"]:
                continue
            a, b = norm_tokens(rows[i]["name"]), norm_tokens(rows[j]["name"])
            if a and b and (is_token_prefix(a, b) or is_token_prefix(b, a)):
                prefix_pairs += 1
    duplicate_clusters = [members for members in by_key.values() if len(members) > 1]
    cross_provider_clusters = [
        members
        for members in duplicate_clusters
        if len({member["provider_id"] for member in members}) > 1
    ]
    duplicate_members = sum(len(members) for members in duplicate_clusters)
    generic_entities = [
        entity
        for entity in entities
        if entity["normalized_name"] in GENERIC_ENTITY_NAMES
        or entity["normalized_name"].startswith("table ")
    ]
    return {
        "entities": len(entities),
        "facts": len(facts),
        "duplicate_clusters": len(duplicate_clusters),
        "cross_provider_clusters": len(cross_provider_clusters),
        "duplicate_ratio": round(duplicate_members / len(entities), 3) if entities else 0.0,
        "generic_entity_ratio": round(len(generic_entities) / len(entities), 3) if entities else 0.0,
        "prefix_duplicate_pairs": prefix_pairs,
    }


def _gate(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    fact_gain = candidate["facts"] - baseline["facts"]
    generic_drop = baseline["generic_entity_ratio"] - candidate["generic_entity_ratio"]
    cross_gain = candidate["cross_provider_clusters"] - baseline["cross_provider_clusters"]
    collapse = baseline["entities"] > 0 and candidate["entities"] < baseline["entities"] * 0.5
    pass_gate = (
        not collapse
        and (
            fact_gain >= max(1, int(baseline["facts"] * 0.10))
            or generic_drop >= 0.05
            or cross_gain >= 1
        )
    )
    return {
        "promote_to_full_reindex": bool(pass_gate),
        "fact_gain": fact_gain,
        "generic_entity_ratio_delta": round(candidate["generic_entity_ratio"] - baseline["generic_entity_ratio"], 3),
        "cross_provider_cluster_gain": cross_gain,
        "entity_collapse_guard": bool(collapse),
        "rule": (
            "promote if no entity collapse and one proxy improves: "
            "facts +10%/+1, generic ratio -0.05, or cross-provider clusters +1"
        ),
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# hq-42k Prompt/Ontology Scenario Gate",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## What Ran",
        "",
        f"- Scenarios smoked: {len(payload['scenarios'])}",
        f"- Providers: {', '.join(payload['providers'])}",
        f"- Cases per scenario: {payload['cases_per_scenario']}",
        "- Full matrix remains cost-gated; this run only decides which candidates deserve full reindex.",
        "",
        "## Result",
        "",
        "| Scenario | Candidate facts | Baseline facts | Generic ratio | Cross-provider clusters | Promote? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["scenarios"]:
        gate = row["gate"]
        cand = row["candidate_census"]
        base = row["baseline_census"]
        lines.append(
            f"| `{row['scenario_id']}` | {cand['facts']} | {base['facts']} | "
            f"{cand['generic_entity_ratio']:.3f} vs {base['generic_entity_ratio']:.3f} | "
            f"{cand['cross_provider_clusters']} vs {base['cross_provider_clusters']} | "
            f"{'yes' if gate['promote_to_full_reindex'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            "A scenario is not judged by answer quality here. It is judged by whether extraction gets better enough to justify a paid full run.",
            "The gate looks for more value-bearing facts, fewer generic entities, or more useful cross-provider duplicate clusters without collapsing entity coverage.",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category-run-prefix", default="fedcat-category-db-v1")
    parser.add_argument("--run-prefix", default="fedcat-scenario-gate-v1")
    parser.add_argument("--scenario-ids", default=",".join(DEFAULT_SCENARIOS),
                        help="comma-separated scenario ids, or 'all'")
    parser.add_argument("--providers", default=",".join(DEFAULT_PROVIDERS),
                        help="comma-separated provider ids, or 'all'")
    parser.add_argument("--cases-per-category", type=int, default=2,
                        help="stratified source case pool size per FinDER category")
    parser.add_argument("--case-pool", choices=("stratified", "full"), default="stratified",
                        help="stratified keeps cases-per-category balance; full uses every source parquet row")
    parser.add_argument("--case-ids", default="",
                        help="optional comma-separated case id filter within the stratified source pool")
    parser.add_argument("--case-id-file", default="",
                        help="optional newline-delimited case id filter, useful for large shards")
    parser.add_argument("--cases-per-scenario", type=int, default=2)
    parser.add_argument("--max-extractions", type=int, default=0,
                        help="optional batch cap for paid extractions in this invocation")
    parser.add_argument("--no-finalize", dest="finalize", action="store_false",
                        help="write partials only; skip aggregate/report writes for parallel shards")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--keep-error-partials", action="store_true",
                        help="when resuming/finalizing, treat existing error partials as recorded failures")
    args = parser.parse_args()

    bc.bootstrap(verbose=False)
    bc.set_global_determinism(42)
    idx = _load_indexer()
    cases = (
        idx.load_cases_full(seed=42)
        if args.case_pool == "full"
        else idx.load_cases_8cat(n_per_cat=args.cases_per_category, seed=42)
    )
    if args.case_ids.strip():
        wanted_case_ids = {cid.strip() for cid in args.case_ids.split(",") if cid.strip()}
        cases = [case for case in cases if case["case_id"] in wanted_case_ids]
    file_case_ids = idx.load_case_id_file(args.case_id_file)
    if file_case_ids:
        cases = [case for case in cases if case["case_id"] in file_case_ids]
    matrix_path = (
        ROOT
        / "outputs"
        / "evaluation"
        / "mdm_fedcat"
        / args.category_run_prefix
        / "prompt_ontology_experiment_matrix.json"
    )
    matrix = _load_matrix(matrix_path)
    scenarios = _scenario_by_id(matrix)
    if args.scenario_ids.strip().lower() == "all":
        wanted_scenarios = [row["scenario_id"] for row in matrix["scenarios"]]
    else:
        wanted_scenarios = [sid.strip() for sid in args.scenario_ids.split(",") if sid.strip()]
    if args.providers.strip().lower() == "all":
        wanted_providers = ["deepseek", "gptoss", "minimax25", "minimax27"]
    else:
        wanted_providers = [pid.strip() for pid in args.providers.split(",") if pid.strip()]
    provider_instances = {
        inst.dept: inst
        for inst in federation.load_instances(MDM_ROOT / "config" / "provider_databases.yaml")
    }
    instances = [provider_instances[pid] for pid in wanted_providers]

    print(
        f"== scenario gate plan: {len(wanted_scenarios)} scenarios × "
        f"{args.cases_per_scenario} cases × {len(instances)} providers = "
        f"{len(wanted_scenarios) * args.cases_per_scenario * len(instances)} paid extractions =="
    )
    if args.max_extractions:
        print(f"   batch cap: stop after {args.max_extractions} new paid extractions")
    for scenario_id in wanted_scenarios:
        scenario = scenarios[scenario_id]
        picked = _pick_cases(cases, scenario, args.cases_per_scenario)
        if len(picked) <= 40:
            case_summary = ", ".join(f"{case['category']}:{case['case_id']}" for case in picked)
        else:
            by_category: dict[str, int] = defaultdict(int)
            for case in picked:
                by_category[case["category"]] += 1
            case_summary = (
                f"{len(picked)} cases; categories="
                + ", ".join(f"{category}:{count}" for category, count in sorted(by_category.items()))
            )
        print(f"   {scenario_id}: {case_summary}")
    if args.dry_run:
        return 0

    _ensure_provider_databases(instances)
    out_dir = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / args.run_prefix
    partial_dir = out_dir / "index_partial"
    partial_dir.mkdir(parents=True, exist_ok=True)

    scenario_records: list[dict[str, Any]] = []
    new_extractions = 0
    stop_requested = False
    for scenario_id in wanted_scenarios:
        if stop_requested:
            break
        scenario = scenarios[scenario_id]
        scenario_slug = _slug(scenario_id)
        extraction = _build_extraction(
            scenario["prompt"]["prompt_id"],
            list(scenario["ontology"]["modules"]),
        )
        picked_cases = _pick_cases(cases, scenario, args.cases_per_scenario)
        candidate_workspaces: dict[tuple[str, str], str] = {}
        baseline_workspaces: dict[tuple[str, str], str] = {}
        results: list[dict[str, Any]] = []
        for case in picked_cases:
            for inst in instances:
                if args.max_extractions and new_extractions >= args.max_extractions:
                    stop_requested = True
                    break
                workspace_id = _scenario_workspace(scenario_slug, inst.dept, case["case_id"])
                candidate_workspaces[(inst.dept, case["case_id"])] = workspace_id
                baseline_workspaces[(inst.dept, case["case_id"])] = workspace_for(
                    inst.dept, case["case_id"]
                )
                partial = partial_dir / f"{scenario_slug}_{inst.dept}_{case['case_id']}.json"
                rec: dict[str, Any] | None = None
                if args.resume and partial.is_file():
                    rec = json.loads(partial.read_text(encoding="utf-8"))
                if rec is not None and (
                    rec.get("prompt_hash") == extraction["prompt_hash"]
                    and rec.get("ontology_hash") == extraction["ontology_hash"]
                    and (not rec.get("error") or args.keep_error_partials)
                ):
                    print(f">>> {scenario_id} {inst.dept} {case['case_id']} - SKIP")
                    results.append(rec)
                    continue
                print(f">>> {scenario_id} {inst.dept} {case['category']} {case['case_id']}")
                rec = _extract_one(
                    inst=inst,
                    case=case,
                    workspace_id=workspace_id,
                    ontology=extraction["ontology"],
                    extraction_tmpl=extraction["extraction_tmpl"],
                )
                rec.update(
                    scenario_id=scenario_id,
                    prompt_id=extraction["prompt_id"],
                    prompt_hash=extraction["prompt_hash"],
                    ontology_modules=scenario["ontology"]["modules"],
                    ontology_hash=extraction["ontology_hash"],
                )
                bc.atomic_write_json(partial, rec)
                new_extractions += 1
                results.append(rec)
                mark = "OK" if not rec["error"] else "ERR"
                print(
                    f"    [{mark}] nodes={rec['nodes_created']} rels={rec['rels_created']} "
                    f"{rec['latency_s']}s" + (f" {rec['error']}" if rec["error"] else "")
                )
            if stop_requested:
                break

        expected = len(picked_cases) * len(instances)
        if len(results) < expected:
            scenario_records.append(
                {
                    "scenario_id": scenario_id,
                    "prompt": scenario["prompt"],
                    "ontology": scenario["ontology"],
                    "cases": [
                        {"case_id": case["case_id"], "category": case["category"]}
                        for case in picked_cases
                    ],
                    "results": results,
                    "status": "partial",
                    "expected_extractions": expected,
                    "completed_extractions": len(results),
                }
            )
            print(
                f"== partial {scenario_id}: {len(results)}/{expected}; "
                "rerun with resume to continue =="
            )
            break

        cand_entities, cand_facts = _read_inventory(instances, candidate_workspaces)
        base_entities, base_facts = _read_inventory(instances, baseline_workspaces)
        cand_census = _census(cand_entities, cand_facts)
        base_census = _census(base_entities, base_facts)
        scenario_record = {
            "scenario_id": scenario_id,
            "prompt": scenario["prompt"],
            "ontology": scenario["ontology"],
            "cases": [
                {"case_id": case["case_id"], "category": case["category"]}
                for case in picked_cases
            ],
            "results": results,
            "status": "complete",
            "candidate_census": cand_census,
            "baseline_census": base_census,
            "gate": _gate(cand_census, base_census),
        }
        scenario_records.append(scenario_record)
        print(
            f"== gate {scenario_id}: promote="
            f"{scenario_record['gate']['promote_to_full_reindex']} "
            f"facts {cand_census['facts']} vs {base_census['facts']}, "
            f"generic {cand_census['generic_entity_ratio']} vs {base_census['generic_entity_ratio']}"
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix,
        "category_run_prefix": args.category_run_prefix,
        "providers": wanted_providers,
        "cases_per_scenario": args.cases_per_scenario,
        "new_extractions_this_run": new_extractions,
        "complete": not stop_requested,
        "scenarios": scenario_records,
    }
    if not args.finalize:
        print("== shard complete; skipped aggregate/report finalization ==")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    bc.atomic_write_json(out_dir / "scenario_gate_aggregate.json", payload)
    _write_report(out_dir / "scenario_gate_report.md", payload)
    print(f"== wrote {(out_dir / 'scenario_gate_aggregate.json').relative_to(ROOT)} ==")
    print(f"== wrote {(out_dir / 'scenario_gate_report.md').relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
