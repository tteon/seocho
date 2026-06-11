#!/usr/bin/env python3
"""Build the three Seocho Capital department graphs — THE ONLY PAID STEP.

Each department extracts the SAME FinDER cases with a DIFFERENT MARA model
(the model is the only moving part — same FIBO `medium` ontology, same
vendor-neutral extraction prompt, same chunking, same store):

    risk        DeepSeek-V3.1   -> mdmrisk
    research    gpt-oss-120b    -> mdmresearch
    compliance  MiniMax-M2.5    -> mdmcompliance

Extraction only — no QA/judging (cost discipline). Cases are a fixed seed-42
stratified pick from dataset/all_slices.csv over slices S1/S2/S6 (metric-rich
+ control), so cross-model value conflicts have somewhere to appear.

Resume-safe like scripts/benchmarks/finder_4arm_sample.py: a per-(dept, case)
partial with matching prompt_hash + ontology_hash is skipped on re-run (the
run prefix is deliberately NOT timestamped). Failures are recorded per
(dept, case), never imputed (§20.2).

Run:  python examples/mdm/02_extract_departments.py --dry-run   # plan + $0
      python examples/mdm/02_extract_departments.py             # paid
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from examples.finder.lib import bench_common as bc  # noqa: E402
from lib.federation import Department  # noqa: E402

DATASET_CSV = ROOT / "dataset" / "all_slices.csv"
REF_SEPARATOR = "===EVIDENCE_BOUNDARY==="

DEPARTMENTS = [
    Department(name="risk", database="mdmrisk", model="DeepSeek-V3.1"),
    Department(name="research", database="mdmresearch", model="gpt-oss-120b"),
    Department(name="compliance", database="mdmcompliance", model="MiniMax-M2.5"),
]

# FIBO `medium` arm (CLAUDE.md §19 Goldilocks): identical for every department.
ONTOLOGY_MODULES = ["be", "ind", "fbc", "dbt", "acc"]

# Metric-rich slices + the single-passage control; n_per_slice=4 → 12 cases.
SLICES = ["S1_FIN_COMP", "S2_FIN_NONQUANT_MULTI", "S6_BASELINE_SINGLE"]


def load_cases(n_per_slice: int, seed: int) -> list[dict]:
    import pandas as pd
    df = pd.read_csv(DATASET_CSV)
    df = df[df["slice"].isin(SLICES)]
    parts = []
    for _, group in df.groupby("slice"):
        take = min(n_per_slice, len(group))
        parts.append(group.sample(n=take, random_state=seed).sort_values("_id"))
    sample = pd.concat(parts, ignore_index=True)
    cases = []
    for _, r in sample.iterrows():
        refs = [x.strip() for x in str(r["references_joined"]).split(REF_SEPARATOR) if x.strip()]
        cases.append({
            "case_id": r["_id"], "slice": r["slice"], "category": r["category"],
            "n_refs": int(r["n_refs"]), "query": r["query"], "references": refs,
        })
    return cases


def extract_one(*, dept: Department, case: dict, ontology, extraction_tmpl) -> dict:
    """Extract one case's gold references into the department DB.

    Opens its OWN Neo4jGraphStore: Seocho.close() closes the store it was
    given, so a shared store would die after the first department.
    """
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    workspace_id = f"mdm-{dept.name}-{case['case_id']}"
    started = time.perf_counter()
    error = ""
    nodes = rels = 0
    client = None
    try:
        graph_store = Neo4jGraphStore(os.environ["NEO4J_URI"],
                                      os.environ.get("NEO4J_USER", "neo4j"),
                                      os.environ.get("NEO4J_PASSWORD", ""))
        llm = create_llm_backend(provider="mara", model=dept.model)
        client = Seocho(ontology=ontology, graph_store=graph_store, llm=llm,
                        workspace_id=workspace_id, extraction_prompt=extraction_tmpl)
        client.default_database = dept.database
        try:
            graph_store.ensure_constraints(ontology, database=dept.database)
        except Exception:
            pass
        for i, ref in enumerate(case["references"], 1):
            print(f"      ref {i}/{len(case['references'])} ({len(ref)} chars)", flush=True)
            client.add(ref, user_id=workspace_id)
        n = graph_store.query("MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                              params={"w": workspace_id}, database=dept.database)
        r = graph_store.query("MATCH ({_workspace_id:$w})-[x]->() RETURN count(x) AS c",
                              params={"w": workspace_id}, database=dept.database)
        nodes, rels = int(n[0]["c"]), int(r[0]["c"])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return {
        "dept": dept.name, "model": dept.model, "database": dept.database,
        "case_id": case["case_id"], "slice": case["slice"], "n_refs": case["n_refs"],
        "workspace_id": workspace_id, "nodes_created": nodes, "rels_created": rels,
        "latency_s": round(time.perf_counter() - started, 2), "error": error,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-slice", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-prefix", default="seocho-capital-v1",
                    help="deliberately NOT timestamped so re-runs resume by default")
    ap.add_argument("--departments", default="risk,research,compliance")
    ap.add_argument("--limit-cases", type=int, default=0, help="cap cases (smoke); 0=all")
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bc.bootstrap(verbose=False)
    bc.set_global_determinism(args.seed)

    from examples.finder.datasets.fibo_modules.compose import compose_modules
    from seocho.query.strategy import PromptTemplate

    ontology = compose_modules(ONTOLOGY_MODULES)
    onto_ctx = ontology.to_extraction_context()
    onto_hash = bc.short_hash(onto_ctx.get("entity_types", "") + "\n" +
                              onto_ctx.get("relationship_types", ""))

    # Vendor-neutral extraction prompt — identical for all three departments
    # (provider "mara" resolves to the neutral prompt; §20.9 mirror).
    system_tmpl, prompt_id, prompt_file = bc.resolve_extraction_prompt("mara")
    prompt_hash = bc.short_hash(system_tmpl)

    sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))
    from finder_4arm_sample import KGPromptTemplate  # composite {{ontology}} var

    extraction_tmpl: PromptTemplate = KGPromptTemplate(
        system=system_tmpl,
        user="Source 10-K text to extract into the graph:\n\n{{text}}",
    )

    depts = [d for d in DEPARTMENTS if d.name in
             {x.strip() for x in args.departments.split(",")}]
    cases = load_cases(args.n_per_slice, args.seed)
    if args.limit_cases:
        cases = cases[: args.limit_cases]

    print(f"== plan: {len(cases)} cases × {len(depts)} departments = "
          f"{len(cases) * len(depts)} extraction runs (PAID: MARA calls) ==")
    print(f"   ontology: medium {ONTOLOGY_MODULES} (hash {onto_hash})")
    print(f"   prompt: {prompt_file} (id={prompt_id}, hash {prompt_hash})")
    for d in depts:
        print(f"   {d.name:<11} {d.model:<14} -> {d.database}")
    if args.dry_run:
        for c in cases:
            print(f"   {c['slice']:<22} {c['case_id']}  n_refs={c['n_refs']}  {c['query'][:60]}")
        print("(dry-run: stopping before any LLM/graph work)")
        return 0

    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry
    graph_store = Neo4jGraphStore(os.environ["NEO4J_URI"],
                                  os.environ.get("NEO4J_USER", "neo4j"),
                                  os.environ.get("NEO4J_PASSWORD", ""))
    for d in depts:
        db_registry.register(d.database)
        graph_store.ensure_database(d.database, wait_online=True, timeout=30.0)
        print(f"== database {d.database} online ==")

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
    out_partial = out_dir / "partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    total = len(cases) * len(depts)
    i = 0
    try:
        for case in cases:
            for dept in depts:
                i += 1
                partial = out_partial / f"{dept.name}_{case['case_id']}.json"
                if args.resume and partial.is_file():
                    try:
                        rec = json.loads(partial.read_text())
                    except Exception:
                        rec = None
                    if (rec and rec.get("prompt_hash") == prompt_hash
                            and rec.get("ontology_hash") == onto_hash
                            and not rec.get("error")):
                        print(f">>> [{i}/{total}] {dept.name} {case['case_id']} — SKIP (resume)")
                        results.append(rec)
                        continue
                print(f">>> [{i}/{total}] {dept.name}({dept.model}) "
                      f"{case['slice']} {case['case_id']}")
                rec = extract_one(dept=dept, case=case, ontology=ontology,
                                  extraction_tmpl=extraction_tmpl)
                rec["prompt_hash"] = prompt_hash
                rec["ontology_hash"] = onto_hash
                rec["seed"] = args.seed
                bc.atomic_write_json(partial, rec)
                mark = "OK" if not rec["error"] else "ERR"
                print(f"    [{mark}] nodes={rec['nodes_created']} rels={rec['rels_created']} "
                      f"{rec['latency_s']}s" + (f"  error: {rec['error']}" if rec["error"] else ""))
                results.append(rec)
    finally:
        graph_store.close()

    attempted = len(results)
    failed = [r for r in results if r.get("error")]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix, "seed": args.seed,
        "ontology_modules": ONTOLOGY_MODULES, "ontology_hash": onto_hash,
        "prompt_id": prompt_id, "prompt_hash": prompt_hash,
        "departments": [{"name": d.name, "model": d.model, "database": d.database}
                        for d in depts],
        "n_cases": len(cases), "attempted": attempted,
        "succeeded": attempted - len(failed), "failed": len(failed),
        "results": results,
    }
    agg = out_dir / "aggregate.json"
    bc.atomic_write_json(agg, payload)
    print(f"\n== wrote {agg.relative_to(ROOT)} ==")
    print(f"== attempted {attempted}, succeeded {attempted - len(failed)}, "
          f"failed {len(failed)} (failures recorded, never imputed) ==")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
