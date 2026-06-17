#!/usr/bin/env python3
"""Multi-agent data federation over MARA provider-agents (hq-42k).

Three measured lanes over the same 16 cases (8 FinDER categories), same answer
prompt + number-aware metric (§20.3):

  silo-<provider>   each model-provider answers from its OWN database/store only
                    (the capability matrix — per (model, category) quality)
  federation        FederationAgent routes per query, fans out to ALL
                    providers, merges (reference→survivorship vote /
                    narrative→concat synthesis)

Then $0-replay analyses over the stored records:
  - per (provider × category) capability matrix
  - federation vs best-single-provider (pre-registered H-FED1/H-FED2)
  - fan-out cost/quality frontier (broadcast vs top-k vs primary, replay)
  - partial-failure degradation (drop k providers, replay)

Cost: silo answers = cases × providers (paid); federation narrative synthesis
= cases routed-narrative (paid, 1 call each); reference merge = $0. Resume-safe.

Run:  python examples/mdm/12_federation_agents.py --dry-run
      python examples/mdm/12_federation_agents.py --limit-cases 1
      python examples/mdm/12_federation_agents.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for p in (str(MDM_ROOT), str(ROOT), str(ROOT / "scripts" / "benchmarks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for _k, _v in dotenv_values(ROOT / ".env").items():
    if _v is not None:
        os.environ.setdefault(_k, _v)

from examples.finder.lib import bench_common as bc, llm_io  # noqa: E402
from finder_4arm_sample import _ANSWER_SYSTEM, evaluate_answer  # noqa: E402
from finder_compare_vector_graph import token_f1  # noqa: E402


def evaluate_both(expected: str, answer: str) -> dict:
    """Number-aware overlap (reference questions) + token-F1 (narrative).

    The 8 FinDER categories are mostly qualitative, where number_overlap is
    near-zero for everyone; token_f1 makes those slices measurable. Both are
    reported so no lane is judged on a metric blind to its question type."""
    m = evaluate_answer(expected, answer or "")
    m["token_f1"] = token_f1(answer or "", expected or "")
    return m
from agents.contracts import ABSTAIN_MARK, FederationRequest  # noqa: E402
from agents.federation_agent import FederationAgent, route_deterministic  # noqa: E402
from agents.provider_agent import ProviderAgent  # noqa: E402
from lib import federation  # noqa: E402
from lib.survivorship import load_ruleset  # noqa: E402

import importlib.util  # noqa: E402


def _load_indexer():
    spec = importlib.util.spec_from_file_location(
        "idx_providers", MDM_ROOT / "11_index_providers.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-prefix", default="fedcat-v1")
    ap.add_argument("--providers-config", default=str(MDM_ROOT / "config" / "providers.yaml"),
                    help="provider instance/database map; use config/provider_databases.yaml "
                         "for the single-DBMS composite-like topology")
    ap.add_argument("--synth-llm", default="mara/MiniMax-M2.7",
                    help="federation narrative synthesizer (loosest RPD by default)")
    ap.add_argument("--n-per-cat", type=int, default=2)
    ap.add_argument("--case-pool", choices=("stratified", "full"), default="stratified",
                    help="stratified keeps n-per-cat balance; full uses every source parquet row")
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

    bc.set_global_determinism(42)
    ruleset = load_ruleset()
    idx = _load_indexer()
    cases = (
        idx.load_cases_full(seed=42)
        if args.case_pool == "full"
        else idx.load_cases_8cat(n_per_cat=args.n_per_cat, seed=42)
    )
    if args.case_ids.strip():
        wanted_case_ids = {cid.strip() for cid in args.case_ids.split(",") if cid.strip()}
        cases = [case for case in cases if case["case_id"] in wanted_case_ids]
    file_case_ids = idx.load_case_id_file(args.case_id_file)
    if file_case_ids:
        cases = [case for case in cases if case["case_id"] in file_case_ids]
    if args.limit_cases:
        cases = cases[: args.limit_cases]

    instances = federation.load_instances(Path(args.providers_config))
    agents = [ProviderAgent(i) for i in instances]
    provider_ids = [a.provider_id for a in agents]

    print(f"== plan: {len(cases)} cases × {len(agents)} providers ==")
    print(f"   silo answers (paid): {len(cases) * len(agents)}")
    n_narr = sum(1 for c in cases if route_deterministic(c["query"]) == "narrative")
    print(f"   federation synthesis (paid, narrative route): ~{n_narr}; "
          f"reference route ({len(cases) - n_narr}) = $0 survivorship vote")
    if args.dry_run:
        for c in cases:
            print(f"   {c['category']:<18} {c['case_id']} route={route_deterministic(c['query'])}")
        return 0

    auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))
    synth_spec = llm_io.parse_llm_spec(args.synth_llm)
    synth_client = llm_io.make_chat_client(synth_spec)
    fed = FederationAgent(providers=agents, ruleset=ruleset,
                          synth_client=synth_client, synth_spec=synth_spec,
                          answer_system=_ANSWER_SYSTEM)

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / args.run_prefix
    out_partial = out_dir / "bench_partial"
    out_partial.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    def cached(name: str):
        p = out_partial / f"{name}.json"
        if args.resume and p.is_file():
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None

    def save(name: str, rec: dict):
        bc.atomic_write_json(out_partial / f"{name}.json", rec)
        records.append(rec)

    total = len(cases) * (len(agents) + 1)
    i = 0
    for case in cases:
        # --- silo lanes (capability matrix) ---
        for a in agents:
            i += 1
            name = f"silo-{a.provider_id}_{case['case_id']}"
            c = cached(name)
            if c is not None and c.get("llm") == f"mara/{a.model}" and not c.get("error"):
                print(f">>> [{i}/{total}] {name} — SKIP")
                records.append(c)
                continue
            resp = a.answer(case["query"], case["case_id"], answer_system=_ANSWER_SYSTEM)
            ev = evaluate_both(case["expected_answer"], resp.answer or "")
            rec = {
                "lane": f"silo-{a.provider_id}", "provider_id": a.provider_id,
                "model": a.model, "case_id": case["case_id"], "slice": case["slice"],
                "category": case["category"], "route": "silo",
                "answer": resp.answer, "evaluation": ev,
                "abstain": resp.abstain, "context_chars": len(resp.context),
                "retrieval_ms": round(resp.retrieval_ms, 1),
                "answer_ms": round(resp.answer_ms, 1),
                "llm": f"mara/{a.model}", "error": resp.error,
            }
            save(name, rec)
            print(f">>> [{i}/{total}] {name}: overlap={ev['number_overlap_ratio']:.2f} "
                  f"abstain={resp.abstain}" + (f" ERR {resp.error}" if resp.error else ""))

        # --- federation lane ---
        i += 1
        name = f"federation_{case['case_id']}"
        c = cached(name)
        if c is not None and not c.get("error"):
            print(f">>> [{i}/{total}] {name} — SKIP")
            records.append(c)
        else:
            req = FederationRequest(query=case["query"], case_id=case["case_id"],
                                    slice_tag=case["slice"], category=case["category"])
            t0 = time.perf_counter()
            fr = fed.answer(req)
            ev = evaluate_both(case["expected_answer"], fr.answer or "")
            rec = {
                "lane": "federation", "case_id": case["case_id"], "slice": case["slice"],
                "category": case["category"], "route": fr.route,
                "answer": fr.answer, "evaluation": ev, "abstain": fr.abstain,
                "context_chars": sum(len(r.context) for r in fr.provider_responses),
                "retrieval_ms": round(sum(fr.fanout_latency_ms.values()), 1),
                "answer_ms": round(fr.answer_ms, 1),
                "providers_attempted": fr.providers_attempted,
                "providers_answered": fr.providers_answered,
                "degraded": fr.degraded, "unavailable": list(fr.unavailable),
                "survived": fr.survived, "llm": args.synth_llm,
                "wall_s": round(time.perf_counter() - t0, 2), "error": "",
            }
            save(name, rec)
            print(f">>> [{i}/{total}] {name}: route={fr.route} "
                  f"overlap={ev['number_overlap_ratio']:.2f} abstain={fr.abstain} "
                  f"answered={fr.providers_answered}/{fr.providers_attempted}")

        # Forced-reference lane ($0 — deterministic survivorship vote over the
        # providers' structured facts). All upstream-FinDER questions route
        # narrative, so this is the only lane that exercises cross-provider
        # MDM golden-record voting; it is free, so we always record it.
        rname = f"federation-ref_{case['case_id']}"
        rc = cached(rname)
        if rc is not None:
            print(f"      [ref] {rname} — SKIP")
            records.append(rc)
        else:
            rreq = FederationRequest(query=case["query"], case_id=case["case_id"],
                                     slice_tag=case["slice"], category=case["category"],
                                     mode="reference")
            rfr = fed.answer(rreq)
            rev = evaluate_both(case["expected_answer"], rfr.answer or "")
            rrec = {
                "lane": "federation-ref", "case_id": case["case_id"],
                "slice": case["slice"], "category": case["category"],
                "route": "reference", "answer": rfr.answer, "evaluation": rev,
                "abstain": rfr.abstain,
                "context_chars": 0, "retrieval_ms": round(sum(rfr.fanout_latency_ms.values()), 1),
                "answer_ms": round(rfr.answer_ms, 1),
                "survived": rfr.survived, "llm": "$0-survivorship", "error": "",
            }
            save(rname, rrec)
            ng = len((rfr.survived or {}).get("golden", []))
            nq = len((rfr.survived or {}).get("quarantined", []))
            print(f"      [ref] $0 vote: {ng} golden, {nq} quarantined facts")

    if not args.finalize:
        print("== shard complete; skipped aggregate finalization ==")
        return 0

    # ---- aggregate + analyses ($0 replay) ----
    summary = _analyze(records, cases, provider_ids)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix, "seed": 42,
        "providers_config": str(Path(args.providers_config)),
        "synth_llm": args.synth_llm, "ruleset_version": ruleset.version,
        "providers": [{"provider_id": a.provider_id, "model": a.model,
                       "uri": a.instance.uri, "database": a.instance.database}
                      for a in agents],
        "categories": sorted({case["category"] for case in cases}),
        "n_cases": len(cases), **summary, "records": records,
    }
    bc.atomic_write_json(out_dir / "federation_aggregate.json", payload)
    _print_report(summary, provider_ids)
    print(f"\n== wrote {(out_dir / 'federation_aggregate.json').relative_to(ROOT)} ==")
    return 0


def _lane_stats(rows: list[dict]) -> dict:
    ov = [r["evaluation"]["number_overlap_ratio"] for r in rows]
    f1 = [r["evaluation"].get("token_f1", 0.0) for r in rows]
    return {
        "n": len(rows),
        "overlap": round(sum(ov) / len(ov), 3) if ov else 0.0,
        "token_f1": round(sum(f1) / len(f1), 3) if f1 else 0.0,
        "abstain": round(sum(1 for r in rows if r["abstain"]) / len(rows), 3) if rows else 0.0,
        "ctx_chars": int(sum(r["context_chars"] for r in rows) / len(rows)) if rows else 0,
    }


def _analyze(records, cases, provider_ids) -> dict:
    by_lane = defaultdict(list)
    for r in records:
        by_lane[r["lane"]].append(r)
    lanes = {lane: _lane_stats(rs) for lane, rs in by_lane.items()}

    # capability matrix: provider × category token-F1 (the metric that varies
    # for these mostly-narrative categories; number_overlap is ~0 across all).
    cats = sorted({c["category"] for c in cases})
    matrix = {}
    for pid in provider_ids:
        row = {}
        for cat in cats:
            rs = [r for r in by_lane.get(f"silo-{pid}", []) if r["category"] == cat]
            row[cat] = round(sum(r["evaluation"].get("token_f1", 0.0) for r in rs) / len(rs), 3) if rs else None
        matrix[pid] = row

    # federation vs best single provider (per case), + abstain coverage (H-FED1/2)
    silo_overall = {pid: lanes.get(f"silo-{pid}", {}).get("token_f1", 0.0) for pid in provider_ids}
    best_silo = max(silo_overall.values()) if silo_overall else 0.0
    fed = lanes.get("federation", {})
    h_fed1 = {"federation": fed.get("token_f1"), "best_silo": round(best_silo, 3),
              "metric": "token_f1",
              "verdict": "SUPPORTED" if fed.get("token_f1", 0) >= best_silo else "REJECTED"}
    min_silo_abstain = min((lanes[f"silo-{p}"]["abstain"] for p in provider_ids
                            if f"silo-{p}" in lanes), default=1.0)
    h_fed2 = {"federation_abstain": fed.get("abstain"),
              "min_silo_abstain": round(min_silo_abstain, 3),
              "verdict": "SUPPORTED" if fed.get("abstain", 1) <= min_silo_abstain else "REJECTED"}

    # fan-out cost/quality frontier ($0 replay): per case, oracle top-1 silo vs
    # federation vs all-silo-union (best of any silo).
    by_case = defaultdict(dict)
    for r in records:
        by_case[r["case_id"]][r["lane"]] = r
    frontier = {"best_single_oracle": [], "federation": []}
    for cid, lr in by_case.items():
        silos = [lr[f"silo-{p}"] for p in provider_ids if f"silo-{p}" in lr]
        if silos:
            frontier["best_single_oracle"].append(
                max(s["evaluation"].get("token_f1", 0.0) for s in silos))
        if "federation" in lr:
            frontier["federation"].append(lr["federation"]["evaluation"].get("token_f1", 0.0))
    frontier_summary = {
        k: round(sum(v) / len(v), 3) if v else 0.0 for k, v in frontier.items()}

    # partial-failure degradation ($0 replay): federation overlap if we drop
    # each k-subset of providers (recompute reference vote / narrative-best from
    # the stored silo answers as a proxy — narrative can't be re-synthesized for
    # free, so we proxy narrative-degraded by best-surviving-silo overlap).
    degradation = _degradation(by_case, provider_ids)

    return {"lanes": lanes, "capability_matrix": matrix,
            "pre_registered": {"H-FED1_fed_ge_best_silo": h_fed1,
                               "H-FED2_union_coverage": h_fed2},
            "fanout_frontier": frontier_summary,
            "partial_failure_degradation": degradation}


def _degradation(by_case, provider_ids) -> dict:
    """$0 proxy: with only a subset of providers available, federation's
    reachable quality ≈ best overlap among the SURVIVING providers' silo
    answers (an upper bound on what any merge could recover). Reported as the
    resilience curve; narrative re-synthesis is not free so this is a proxy."""
    out = {}
    n = len(provider_ids)
    for drop in range(0, n):                 # drop 0..n-1 providers
        keep = n - drop
        if keep == 0:
            break
        per_case = []
        for cid, lr in by_case.items():
            best_over_subsets = []
            for subset in combinations(provider_ids, keep):
                vals = [lr[f"silo-{p}"]["evaluation"].get("token_f1", 0.0)
                        for p in subset if f"silo-{p}" in lr]
                if vals:
                    best_over_subsets.append(max(vals))
            if best_over_subsets:
                # average reachable quality across which subset survives
                per_case.append(sum(best_over_subsets) / len(best_over_subsets))
        out[f"providers_{keep}"] = round(sum(per_case) / len(per_case), 3) if per_case else 0.0
    return out


def _print_report(summary, provider_ids):
    print("\nlane              |  n | token_f1 | num_ov | abstain | ctx chars")
    print("-" * 62)
    for lane, v in sorted(summary["lanes"].items()):
        print(f"{lane:<17} | {v['n']:>2} | {v['token_f1']:.3f}    | "
              f"{v['overlap']:.3f}  | {v['abstain']:.2f}    | {v['ctx_chars']:>9}")
    print("\ncapability matrix (token_f1 by provider × category):")
    cats = sorted(next(iter(summary["capability_matrix"].values())).keys())
    print(f"  {'provider':<11} | " + " | ".join(c[:9] for c in cats))
    for pid, row in summary["capability_matrix"].items():
        print(f"  {pid:<11} | " + " | ".join(
            (f"{row[c]:.2f}" if row[c] is not None else "  - ").rjust(9) for c in cats))
    pr = summary["pre_registered"]
    print(f"\nH-FED1 fed≥best-silo: {pr['H-FED1_fed_ge_best_silo']['verdict']} "
          f"(fed {pr['H-FED1_fed_ge_best_silo']['federation']} vs best silo "
          f"{pr['H-FED1_fed_ge_best_silo']['best_silo']})")
    print(f"H-FED2 union coverage: {pr['H-FED2_union_coverage']['verdict']} "
          f"(fed abstain {pr['H-FED2_union_coverage']['federation_abstain']} vs "
          f"min silo {pr['H-FED2_union_coverage']['min_silo_abstain']})")
    print(f"fan-out frontier: federation {summary['fanout_frontier']['federation']} "
          f"vs best-single-oracle {summary['fanout_frontier']['best_single_oracle']}")
    print(f"partial-failure degradation (reachable overlap by #providers): "
          f"{summary['partial_failure_degradation']}")


if __name__ == "__main__":
    raise SystemExit(main())
