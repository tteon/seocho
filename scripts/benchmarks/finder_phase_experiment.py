#!/usr/bin/env python3
"""FinDER phase experiment — FIBO-graph treatment vs (optional) empty baseline.

For each phase (P0, P1A, P1B, P1C, P1D) and case, run treatment (FIBO module
composition) and optionally a baseline (empty ontology). Each run:
  - ingests the case's evidence chunks via Seocho.local() with the chosen
    ontology + Kimi K2.5 LLM (meta prompt prepended)
  - asks the question with reasoning_mode=True and repair_budget=2 (T2.1)
  - emits an Opik trace with the standardized 5-tag identification set

Outputs:
  outputs/evaluation/finder_phase_experiment/<run_prefix>/aggregate.json
  outputs/evaluation/finder_phase_experiment/<run_prefix>/partial/<phase>_<case>_<variant>.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.finder.lib import bench_common as bc  # noqa: E402


REF_SEPARATOR = "===EVIDENCE_BOUNDARY==="
_NUM_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_cases() -> dict[str, dict]:
    import pandas as pd
    csv_path = ROOT / ".seocho/datasets/finder/slices/all_slices.csv"
    if not csv_path.is_file():
        raise SystemExit(
            f"Missing slice CSV at {csv_path}. Run scripts/benchmarks/finder_build_slices.py first."
        )
    df = pd.read_csv(csv_path)
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        refs = str(r["references_joined"]).split(REF_SEPARATOR)
        refs = [ref.strip() for ref in refs if ref.strip()]
        out[r["_id"]] = {
            "case_id": r["_id"],
            "slice": r["slice"],
            "category": r["category"],
            "type": r["type"] if isinstance(r["type"], str) else "",
            "n_refs": int(r["n_refs"]),
            "query": r["query"],
            "expected_answer": r["answer"],
            "references": refs,
        }
    return out


# ---------------------------------------------------------------------------
# Answer evaluation (number-aware)
# ---------------------------------------------------------------------------

def _extract_numbers(text: str) -> list[str]:
    return [n.replace(",", "").strip().lower() for n in _NUM_RE.findall(text or "")]


def evaluate_answer(expected: str, actual: str) -> dict:
    if not actual:
        return {
            "exact_match": False,
            "contains_match": False,
            "shared_numbers": 0,
            "expected_number_count": len(_extract_numbers(expected)),
            "actual_number_count": 0,
            "number_overlap_ratio": 0.0,
        }
    exp_nums = set(_extract_numbers(expected))
    act_nums = set(_extract_numbers(actual))
    shared = exp_nums & act_nums
    return {
        "exact_match": expected.strip().lower() == actual.strip().lower(),
        "contains_match": expected.strip().lower() in actual.strip().lower(),
        "shared_numbers": len(shared),
        "expected_number_count": len(exp_nums),
        "actual_number_count": len(act_nums),
        "number_overlap_ratio": (len(shared) / len(exp_nums)) if exp_nums else 0.0,
    }


# ---------------------------------------------------------------------------
# Ontology hash (matches bench_common.short_hash convention)
# ---------------------------------------------------------------------------

def _ontology_hash(ontology) -> str:
    try:
        ctx = ontology.to_extraction_context()
        blob = ctx.get("entity_types", "") + "\n" + ctx.get("relationship_types", "")
    except Exception:
        try:
            blob = json.dumps(
                {"nodes": sorted(ontology.nodes.keys()),
                 "rels": sorted(ontology.relationships.keys())},
                sort_keys=True,
            )
        except Exception:
            blob = repr(ontology)
    return bc.short_hash(blob)


# ---------------------------------------------------------------------------
# Run a single (phase × case × variant)
# ---------------------------------------------------------------------------

def run_one(
    *,
    phase: bc.PhaseSpec,
    case: dict,
    variant: str,
    ontology_modules: tuple[str, ...],
    llm_spec: str,
    workspace_prefix: str,
    meta_prompt: str,
    reasoning_mode: bool,
    repair_budget: int,
    out_partial_dir: Path,
) -> dict:
    """Run one (phase × case × variant). Backend: Neo4j if NEO4J_URI set, else LadyBug."""
    from seocho import Seocho
    from seocho.store.llm import create_llm_backend
    from seocho.store.graph import LadybugGraphStore, Neo4jGraphStore

    sys.path.insert(0, str(ROOT))
    from examples.finder.datasets.fibo_modules.compose import compose_modules

    ontology = compose_modules(list(ontology_modules))
    workspace_id = bc.workspace_id_for(phase.code, case["case_id"], variant, prefix=workspace_prefix)
    trace_name = f"{phase.code}/{case['case_id']}/{variant}"

    neo4j_uri = os.environ.get("NEO4J_URI") or os.environ.get("BOLT_URL")
    use_neo4j = bool(neo4j_uri)
    if use_neo4j:
        ladybug_path = None  # not used in this branch
    else:
        ladybug_path = bc.fresh_ladybug(bc.DEFAULT_LBUG_DIR / f"{phase.code}_{case['case_id']}_{variant}.lbug")

    onto_hash = _ontology_hash(ontology)
    prompt_hash = bc.short_hash(meta_prompt) if meta_prompt else "noprompt"
    modules_label = "+".join(ontology_modules) or "baseline"
    dataset_index = f"{case['slice']}/{case['case_id']}"

    trace_tags = bc.opik_tags(
        llm_spec=llm_spec,
        dataset_index=dataset_index,
        prompt_hash=prompt_hash,
        ontology_hash=onto_hash,
        modules=modules_label,
        extra={
            "phase": phase.code,
            "variant": variant,
            "slice": case["slice"],
            "category": case["category"],
            "case": case["case_id"],
            "meta_prompt": "on" if meta_prompt else "off",
            "reasoning_mode": str(reasoning_mode).lower(),
            "repair_budget": str(repair_budget),
        },
    )
    trace_metadata = {
        "phase_name": phase.name,
        "rationale": phase.rationale,
        "ontology_modules": list(ontology_modules),
        "ontology_hash": onto_hash,
        "ontology_node_count": len(ontology.nodes),
        "ontology_rel_count": len(ontology.relationships),
        "prompt_hash": prompt_hash,
        "prompt_chars": len(meta_prompt) if meta_prompt else 0,
        "llm_spec": llm_spec,
        "case_query": case["query"],
        "case_n_refs": case["n_refs"],
        "case_slice": case["slice"],
        "case_category": case["category"],
        "workspace_prefix": workspace_prefix,
        "reasoning_mode": reasoning_mode,
        "repair_budget": repair_budget,
    }
    print(
        f"    {trace_name}: tags=[model:{llm_spec}, dataset_index:{dataset_index}, "
        f"prompt_hash:{prompt_hash}, ontology_hash:{onto_hash}, modules:{modules_label}]",
        flush=True,
    )

    started = time.perf_counter()
    add_latency_ms = 0.0
    ask_latency_ms = 0.0
    answer = ""
    error = ""
    nodes_created = 0
    rels_created = 0

    client = None
    try:
        provider, model = (llm_spec.split("/", 1) if "/" in llm_spec else ("openai", llm_spec))
        raw_backend = create_llm_backend(provider=provider.strip(), model=model.strip())
        wrapped_llm = bc.MetaPromptLLMWrapper(raw_backend, meta_prompt) if meta_prompt else raw_backend

        if use_neo4j:
            graph_store = Neo4jGraphStore(
                neo4j_uri,
                os.environ.get("NEO4J_USER", "neo4j"),
                os.environ.get("NEO4J_PASSWORD", ""),
            )
        else:
            graph_store = LadybugGraphStore(str(ladybug_path))
        try:
            graph_store.ensure_constraints(ontology)
        except Exception:
            pass

        client = Seocho(
            ontology=ontology,
            graph_store=graph_store,
            llm=wrapped_llm,
            workspace_id=workspace_id,
        )

        timing = {"add_ms": 0.0, "ask_ms": 0.0}

        def _phase_work():
            t0 = time.perf_counter()
            for i, ref in enumerate(case["references"], 1):
                print(
                    f"    {trace_name}: add ref {i}/{len(case['references'])} ({len(ref)} chars)",
                    flush=True,
                )
                client.add(ref, user_id=workspace_id)
            timing["add_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            print(f"    {trace_name}: add done in {timing['add_ms']}ms", flush=True)

            t1 = time.perf_counter()
            print(f"    {trace_name}: ask…", flush=True)
            _answer = client.ask(
                case["query"],
                user_id=workspace_id,
                session_id=f"{phase.code}-{case['case_id']}-{variant}",
                reasoning_mode=reasoning_mode,
                repair_budget=repair_budget,
            )
            timing["ask_ms"] = round((time.perf_counter() - t1) * 1000.0, 2)
            print(f"    {trace_name}: ask done in {timing['ask_ms']}ms", flush=True)
            bc.set_opik_trace_metadata(name=trace_name, tags=trace_tags, metadata=trace_metadata)
            return _answer

        answer = bc.run_under_opik_track(
            name=trace_name,
            tags=trace_tags,
            metadata=trace_metadata,
            work_fn=_phase_work,
        )
        add_latency_ms = timing["add_ms"]
        ask_latency_ms = timing["ask_ms"]

        # Direct graph count (T2.8). For Neo4j (shared DB) we filter by
        # workspace_id AND target the ontology-derived database (Seocho routes
        # writes to e.g. 'fibobeindlpg', not the default 'neo4j').
        try:
            if use_neo4j:
                target_db = getattr(client, "default_database", None) or "neo4j"
                n_rows = graph_store.query(
                    "MATCH (n {_workspace_id:$wid}) RETURN count(n) AS c",
                    params={"wid": workspace_id},
                    database=target_db,
                )
                r_rows = graph_store.query(
                    "MATCH (a {_workspace_id:$wid})-[r]->() RETURN count(r) AS c",
                    params={"wid": workspace_id},
                    database=target_db,
                )
            else:
                n_rows = graph_store.query("MATCH (n) RETURN count(n) AS c")
                r_rows = graph_store.query("MATCH ()-[r]->() RETURN count(r) AS c")
            nodes_created = int(n_rows[0]["c"]) if n_rows else 0
            rels_created = int(r_rows[0]["c"]) if r_rows else 0
        except Exception:
            pass
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    metrics = evaluate_answer(case["expected_answer"], answer)
    total_ms = round((time.perf_counter() - started) * 1000.0, 2)

    result = {
        "phase": phase.code,
        "phase_name": phase.name,
        "case_id": case["case_id"],
        "case_slice": case["slice"],
        "case_category": case["category"],
        "variant": variant,
        "ontology_modules": list(ontology_modules),
        "ontology_node_count": len(ontology.nodes),
        "ontology_rel_count": len(ontology.relationships),
        "ontology_hash": onto_hash,
        "prompt_hash": prompt_hash,
        "model": llm_spec,
        "dataset_index": dataset_index,
        "workspace_id": workspace_id,
        "ladybug_path": str(ladybug_path) if ladybug_path else None,
        "graph_backend": "neo4j" if use_neo4j else "ladybug",
        "query": case["query"],
        "expected_answer": case["expected_answer"],
        "answer": answer,
        "evaluation": metrics,
        "latency_ms": {
            "add_total": add_latency_ms,
            "ask": ask_latency_ms,
            "total": total_ms,
        },
        "nodes_created": nodes_created,
        "relationships_created": rels_created,
        "reasoning_mode": reasoning_mode,
        "repair_budget": repair_budget,
        "meta_prompt_applied": bool(meta_prompt),
        "error": error,
    }

    # Atomic partial checkpoint
    partial_path = out_partial_dir / f"{phase.code}_{case['case_id']}_{variant}.json"
    try:
        bc.atomic_write_json(partial_path, result)
    except Exception as exc:
        print(f"  [warn] partial write failed: {exc}", flush=True)
    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _phase_summary(results: list[dict]) -> dict:
    by_phase: dict[str, dict] = {}
    for r in results:
        by_phase.setdefault(r["phase"], {"baseline": [], "treatment": []}).setdefault(r["variant"], []).append(r)
    summary = {}
    for p, by_variant in by_phase.items():
        row = {"phase": p, "n_cases": len(by_variant.get("treatment") or by_variant.get("baseline") or [])}
        for v in ("baseline", "treatment"):
            runs = by_variant.get(v) or []
            if not runs:
                continue
            overlaps = [r["evaluation"]["number_overlap_ratio"] for r in runs]
            contains = [r["evaluation"]["contains_match"] for r in runs]
            asks = [r["latency_ms"]["ask"] for r in runs]
            row[f"{v}_number_overlap_mean"] = round(sum(overlaps) / len(overlaps), 3)
            row[f"{v}_contains_rate"] = round(sum(contains) / len(contains), 3)
            row[f"{v}_ask_ms_mean"] = round(sum(asks) / len(asks), 1)
        summary[p] = row
    return summary


def _print_table(phase_summary: dict) -> None:
    print("\nphase | n  | baseline overlap → treatment overlap | baseline contains → treatment contains | ask_ms b→t")
    print("-" * 110)
    for p, row in phase_summary.items():
        bo = row.get("baseline_number_overlap_mean", 0.0)
        to_ = row.get("treatment_number_overlap_mean", 0.0)
        bcm = row.get("baseline_contains_rate", 0.0)
        tc = row.get("treatment_contains_rate", 0.0)
        bm = row.get("baseline_ask_ms_mean", 0.0)
        tm = row.get("treatment_ask_ms_mean", 0.0)
        delta = to_ - bo
        print(
            f"{p:5s} | {row['n_cases']:2d} | {bo:.3f} → {to_:.3f}  (Δ={delta:+.3f})       "
            f"| {bcm:.2f} → {tc:.2f}                  | {bm:.0f} → {tm:.0f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phases", default="all", help="Comma list of phase codes (P0,P1A,P1B,P1C,P1D) or 'all'.")
    parser.add_argument("--llm", default=os.environ.get("SEOCHO_LLM", "kimi/kimi-k2.5"))
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls; report plan only.")
    parser.add_argument("--workspace-prefix",
                        default=f"finder-phase-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--no-meta-prompt", action="store_true")
    parser.add_argument(
        "--variants",
        default="treatment",
        choices=("treatment", "baseline", "both"),
        help="Default 'treatment' (FIBO-only). Use 'both' to also run an empty-ontology baseline.",
    )
    parser.add_argument("--reasoning-mode", action="store_true", default=True,
                        help="Enable SEOCHO reasoning loop (default True per T2.1).")
    parser.add_argument("--no-reasoning-mode", dest="reasoning_mode", action="store_false")
    parser.add_argument("--repair-budget", type=int, default=2,
                        help="Number of repair attempts in ask() reasoning loop (default 2 per T2.1).")
    args = parser.parse_args()

    bc.bootstrap(verbose=True)
    meta_prompt = "" if args.no_meta_prompt else bc.load_meta_prompt()
    if meta_prompt:
        print(f"== meta prompt loaded ({len(meta_prompt)} chars, hash={bc.short_hash(meta_prompt)}) ==")
    else:
        print("== meta prompt: disabled or missing ==")

    report = bc.preflight(
        strict=not args.dry_run,
        require_moonshot=not args.dry_run,
        require_openai_embed=False,
        require_neo4j=False,
        require_opik=False,
        require_slices=True,
    )
    report.print_table()
    if not args.dry_run and not report.ok:
        raise SystemExit("preflight failed — fix env/connectivity before running")

    # Resolve phase selection
    if args.phases == "all":
        selected_phases = bc.PHASES
    else:
        wanted = {p.strip().upper() for p in args.phases.split(",")}
        selected_phases = tuple(p for p in bc.PHASES if p.code in wanted)
        if not selected_phases:
            raise SystemExit(f"No matching phases for filter {args.phases!r}")

    cases_index = load_cases()
    plan: list[tuple[bc.PhaseSpec, dict]] = []
    for phase in selected_phases:
        for case_spec in phase.cases:
            case = cases_index.get(case_spec.case_id)
            if case is None:
                print(f"  ! case {case_spec.case_id} not found in slices CSV — skipping")
                continue
            plan.append((phase, case))

    variant_plan: list[tuple[str, tuple[str, ...] | None]] = []
    if args.variants in ("baseline", "both"):
        variant_plan.append(("baseline", ()))
    if args.variants in ("treatment", "both"):
        variant_plan.append(("treatment", None))  # resolved per-phase below

    print(f"\n== plan ({len(plan)} cases × {len(variant_plan)} variants = {len(plan)*len(variant_plan)} runs) ==")
    for phase, case in plan:
        print(f"  {phase.code:5s} {case['case_id']} [{case['slice']}]  {case['query'][:80]}")

    if args.dry_run:
        print("\n(dry-run: stopping before LLM calls)")
        return 0

    from seocho.tracing import configure_tracing_from_env, current_backend_names, flush_tracing
    tracing_on = configure_tracing_from_env()
    print(f"\n== tracing ==\n  enabled: {tracing_on}  backends: {current_backend_names()}\n")

    run_prefix = args.workspace_prefix
    out_dir = ROOT / "outputs" / "evaluation" / "finder_phase_experiment" / run_prefix
    out_partial_dir = out_dir / "partial"
    out_partial_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    started = time.perf_counter()
    for i, (phase, case) in enumerate(plan, 1):
        for variant, modules in variant_plan:
            if variant == "treatment":
                modules = phase.treatment_modules
            label = f"[{i}/{len(plan)}] {phase.code} {case['case_id']} ({variant})"
            print(f"\n>>> {label}")
            res = run_one(
                phase=phase,
                case=case,
                variant=variant,
                ontology_modules=modules,
                llm_spec=args.llm,
                workspace_prefix=args.workspace_prefix,
                meta_prompt=meta_prompt,
                reasoning_mode=args.reasoning_mode,
                repair_budget=args.repair_budget,
                out_partial_dir=out_partial_dir,
            )
            mark = "OK" if not res["error"] else "ERR"
            ev = res["evaluation"]
            print(
                f"    {mark}  ask={res['latency_ms']['ask']}ms"
                f"  numbers={ev['shared_numbers']}/{ev['expected_number_count']}"
                f"  contains={ev['contains_match']}"
                f"  nodes={res['nodes_created']} rels={res['relationships_created']}"
            )
            if res["error"]:
                print(f"    error: {res['error']}")
            results.append(res)

    try:
        flush_tracing()
    except Exception:
        pass
    total_s = round(time.perf_counter() - started, 2)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": run_prefix,
        "workspace_prefix": args.workspace_prefix,
        "llm": args.llm,
        "preflight": report.to_dict(),
        "tracing_backends": current_backend_names(),
        "opik_project": os.environ.get("OPIK_PROJECT_NAME", ""),
        "opik_workspace": os.environ.get("OPIK_WORKSPACE", ""),
        "total_runs": len(results),
        "total_wall_seconds": total_s,
        "reasoning_mode": args.reasoning_mode,
        "repair_budget": args.repair_budget,
        "meta_prompt_hash": bc.short_hash(meta_prompt) if meta_prompt else "noprompt",
        "results": results,
        "phase_summary": _phase_summary(results),
    }

    aggregate_path = out_dir / "aggregate.json"
    bc.atomic_write_json(aggregate_path, summary)
    print(f"\n== wrote {aggregate_path.relative_to(ROOT)} ==")
    _print_table(summary["phase_summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
