#!/usr/bin/env python3
"""Index FinDER across ALL 8 categories into each MARA provider's store (hq-42k).

THE PAID STEP. Each of the 4 MARA model-providers (config/providers.yaml)
extracts the SAME stratified case set — 2 cases × 8 FinDER categories = 16
cases — into its own sovereign DozerDB instance (replicate-and-route, per the
traffic-eng panel). Resume-safe per-(provider, case) partials; failures
recorded, never imputed (§20.2).

Run:  python examples/mdm/11_index_providers.py --dry-run        # plan, $0
      python examples/mdm/11_index_providers.py --limit-cases 1  # smoke (4 calls)
      python examples/mdm/11_index_providers.py                  # full (64 calls)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for p in (str(MDM_ROOT), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import os  # noqa: E402

from examples.finder.lib import bench_common as bc  # noqa: E402
from agents.provider_agent import ProviderAgent, workspace_for  # noqa: E402
from lib import federation  # noqa: E402

DATASET_CSV = ROOT / "dataset" / "all_slices.csv"
SOURCE_PARQUET = ROOT / ".seocho" / "datasets" / "finder" / "data" / "train-00000-of-00001.parquet"
REF_SEPARATOR = "===EVIDENCE_BOUNDARY==="
ONTOLOGY_MODULES = ["be", "ind", "fbc", "dbt", "acc"]   # medium FIBO arm
FINDER_CATEGORIES = ["Company overview", "Financials", "Footnotes", "Governance",
                     "Accounting", "Shareholder return", "Legal", "Risk"]


def load_cases_8cat(n_per_cat: int, seed: int) -> list[dict]:
    """Stratified sample across ALL 8 FinDER categories from the upstream parquet.

    all_slices.csv only carries 3 categories; the full 8 live in the source
    parquet. References there are a list column (not the joined CSV form)."""
    import pandas as pd
    df = pd.read_parquet(SOURCE_PARQUET)
    parts = []
    for cat in FINDER_CATEGORIES:
        grp = df[df["category"] == cat]
        if grp.empty:
            print(f"  [warn] category absent in source: {cat}")
            continue
        parts.append(grp.sample(n=min(n_per_cat, len(grp)), random_state=seed)
                     .sort_values("_id"))
    sample = pd.concat(parts, ignore_index=True)
    cases = []
    for _, r in sample.iterrows():
        refs = r["references"]
        if hasattr(refs, "tolist"):
            refs = refs.tolist()
        refs = [str(x).strip() for x in (refs or []) if str(x).strip()]
        cases.append({
            "case_id": str(r["_id"]), "slice": f"CAT_{r['category'][:4].upper()}",
            "category": str(r["category"]), "n_refs": len(refs),
            "query": str(r["text"]), "expected_answer": str(r["answer"]),
            "references": refs,
        })
    return cases


def load_cases_full(seed: int) -> list[dict]:
    """Load the full upstream FinDER parquet across all available categories."""
    import pandas as pd

    df = pd.read_parquet(SOURCE_PARQUET)
    df = df.sample(frac=1.0, random_state=seed).sort_values(["category", "_id"])
    cases = []
    for _, r in df.iterrows():
        refs = r["references"]
        if hasattr(refs, "tolist"):
            refs = refs.tolist()
        refs = [str(x).strip() for x in (refs or []) if str(x).strip()]
        category = str(r["category"])
        cases.append({
            "case_id": str(r["_id"]), "slice": f"CAT_{category[:4].upper()}",
            "category": category, "n_refs": len(refs),
            "query": str(r["text"]), "expected_answer": str(r["answer"]),
            "references": refs,
        })
    return cases


def load_case_id_file(path: str) -> set[str]:
    if not path:
        return set()
    return {
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-cat", type=int, default=2)
    ap.add_argument("--case-pool", choices=("stratified", "full"), default="stratified",
                    help="stratified keeps n-per-cat balance; full uses every source parquet row")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-prefix", default="fedcat-v1")
    ap.add_argument("--providers-config", default=str(MDM_ROOT / "config" / "providers.yaml"))
    ap.add_argument("--providers", default="deepseek,gptoss,minimax25,minimax27")
    ap.add_argument("--case-ids", default="",
                    help="optional comma-separated case id filter within the stratified source pool")
    ap.add_argument("--case-id-file", default="",
                    help="optional newline-delimited case id filter, useful for large shards")
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--no-finalize", dest="finalize", action="store_false",
                    help="write partials only; skip aggregate finalization for parallel shards")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bc.bootstrap(verbose=False)
    bc.set_global_determinism(args.seed)

    from examples.finder.datasets.fibo_modules.compose import compose_modules
    from seocho.query.strategy import PromptTemplate
    sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))
    from finder_4arm_sample import KGPromptTemplate

    ontology = compose_modules(ONTOLOGY_MODULES)
    octx = ontology.to_extraction_context()
    onto_hash = bc.short_hash(octx.get("entity_types", "") + "\n" + octx.get("relationship_types", ""))
    system_tmpl, prompt_id, prompt_file = bc.resolve_extraction_prompt("mara")
    prompt_hash = bc.short_hash(system_tmpl)
    extraction_tmpl: PromptTemplate = KGPromptTemplate(
        system=system_tmpl, user="Source 10-K text to extract into the graph:\n\n{{text}}")

    all_instances = {i.dept: i for i in federation.load_instances(Path(args.providers_config))}
    want = [x.strip() for x in args.providers.split(",") if x.strip()]
    agents = [ProviderAgent(all_instances[p]) for p in want if p in all_instances]

    cases = (
        load_cases_full(args.seed)
        if args.case_pool == "full"
        else load_cases_8cat(args.n_per_cat, args.seed)
    )
    if args.case_ids.strip():
        wanted_case_ids = {cid.strip() for cid in args.case_ids.split(",") if cid.strip()}
        cases = [case for case in cases if case["case_id"] in wanted_case_ids]
    file_case_ids = load_case_id_file(args.case_id_file)
    if file_case_ids:
        cases = [case for case in cases if case["case_id"] in file_case_ids]
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    print(f"== plan: {len(cases)} cases × {len(agents)} providers = "
          f"{len(cases) * len(agents)} extractions (PAID) ==")
    print(f"   ontology medium {ONTOLOGY_MODULES} (hash {onto_hash}); "
          f"prompt {prompt_file} (hash {prompt_hash})")
    by_cat = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"   categories: {by_cat}")
    for a in agents:
        print(f"   {a.provider_id:<11} {a.model:<14} -> {a.instance.uri}")
    if args.dry_run:
        for c in cases:
            print(f"   {c['category']:<18} {c['case_id']}  {c['query'][:60]}")
        return 0

    # Ensure each provider DB online (readiness gate).
    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry
    for a in agents:
        gs = Neo4jGraphStore(a.instance.uri, os.environ.get("NEO4J_USER", "neo4j"),
                             os.environ.get("NEO4J_PASSWORD", ""))
        try:
            # One instance == one provider, so each uses the default "neo4j" DB
            # (reserved name — always exists, never ensure/create it). Just probe
            # readiness with a trivial query.
            if a.instance.database != "neo4j":
                db_registry.register(a.instance.database)
                gs.ensure_database(a.instance.database, wait_online=True)
            else:
                gs.query("RETURN 1 AS ok", database="neo4j")
        finally:
            gs.close()

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / args.run_prefix
    out_partial = out_dir / "index_partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    results = []
    total = len(cases) * len(agents)
    i = 0
    for case in cases:
        for a in agents:
            i += 1
            partial = out_partial / f"{a.provider_id}_{case['case_id']}.json"
            if args.resume and partial.is_file():
                rec = json.loads(partial.read_text())
                if (rec.get("prompt_hash") == prompt_hash
                        and rec.get("ontology_hash") == onto_hash
                        and not rec.get("error")):
                    print(f">>> [{i}/{total}] {a.provider_id} {case['case_id']} — SKIP (resume)")
                    results.append(rec)
                    continue
            print(f">>> [{i}/{total}] {a.provider_id}({a.model}) "
                  f"{case['category']} {case['case_id']}")
            rec = a.index(case, ontology=ontology, extraction_tmpl=extraction_tmpl)
            rec.update(prompt_hash=prompt_hash, ontology_hash=onto_hash, seed=args.seed)
            bc.atomic_write_json(partial, rec)
            mark = "OK" if not rec["error"] else "ERR"
            print(f"    [{mark}] nodes={rec['nodes_created']} rels={rec['rels_created']} "
                  f"{rec['latency_s']}s" + (f"  {rec['error']}" if rec["error"] else ""))
            results.append(rec)

    if not args.finalize:
        print("== shard complete; skipped aggregate finalization ==")
        return 0

    failed = [r for r in results if r.get("error")]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix, "seed": args.seed,
        "ontology_modules": ONTOLOGY_MODULES, "ontology_hash": onto_hash,
        "prompt_id": prompt_id, "prompt_hash": prompt_hash,
        "providers": [{"provider_id": a.provider_id, "model": a.model,
                       "uri": a.instance.uri} for a in agents],
        "categories": FINDER_CATEGORIES, "n_cases": len(cases),
        "attempted": len(results), "failed": len(failed), "results": results,
    }
    bc.atomic_write_json(out_dir / "index_aggregate.json", payload)
    print(f"\n== indexed; attempted {len(results)}, failed {len(failed)} "
          f"(recorded, never imputed) ==")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
