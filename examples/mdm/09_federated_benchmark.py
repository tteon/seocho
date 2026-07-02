#!/usr/bin/env python3
"""Multi-agent federation benchmark — 5 lanes × the same 12 FinDER cases.

The measurement this demo exists for: is multi-instance federation + MDM
consolidation WORTH it, on numbers, against the department silos?

Five "agents" answer every FinDER question, all through the SAME answer LLM,
same prompt, same metric (§20.3 fairness — only the retrieval context differs):

  silo-risk        Risk dept agent: subgraph from dozer-risk ONLY
  silo-research    Research dept agent: dozer-research ONLY
  silo-compliance  Compliance dept agent: dozer-compliance ONLY
  federation       Federation agent: LIVE fan-out union across all 3 physical
                   instances per query (raw union, conflicts visible, no MDM)
  gold             Steward agent: consolidated mdmmaster golden records with
                   confidence + per-instance provenance + quarantine notices

Pre-registered hypotheses (§20.4 — report verdicts INCLUDING disconfirming):

  H-FED1 (consolidation quality): mean number-overlap of `gold` >= the best
         single silo. Mechanism: union coverage + conflict resolution.
  H-FED2 (coverage): abstain rate ("not in the provided context") of `gold`
         and `federation` <= every silo's. Silos can only see their model's
         extraction.
  H-FED3 (federation cost): `federation` quality ~= `gold` (same information
         union) BUT pays per-query costs: retrieval latency (3 bolt
         round-trips) and a larger, conflict-bearing context. Consolidation
         amortizes federation work into the gold tier (the medallion
         argument).

Honesty notes: 2 of 12 cases have NO case anchor (no model extracted a
company-like entity) — the gold lane reports "no golden record", counted as
abstention, not dropped. Quality is gated upstream by extraction recall
(generator-dependent); a silo losing because its model extracted little is a
finding, not a bug (§20.8).

Cost: 12 cases × 5 lanes = 60 MARA chat calls (default DeepSeek-V3.1).
Resume-safe: per-(case, lane) partials are skipped on re-run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from examples.finder.lib import bench_common as bc  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402
from lib import federation  # noqa: E402

# Reuse the 4-arm benchmark's answer system prompt + number-aware metric and
# the extraction script's case sampler — same cases, same metric (§20.3).
from finder_4arm_sample import _ANSWER_SYSTEM, evaluate_answer  # noqa: E402


def _load_extractor_module():
    spec = importlib.util.spec_from_file_location(
        "mdm_extract", MDM_ROOT / "02_extract_departments.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_INFRA = set(federation.INFRA_LABELS)
ABSTAIN_MARK = "not in the provided context"


# ---------------------------------------------------------------------------
# Context builders (the ONLY thing that differs between lanes)
# ---------------------------------------------------------------------------

def silo_context(driver, database: str, ws: str, dept: str, model: str) -> str:
    """Graph-as-context from ONE physical instance (mirrors the 4-arm
    serializer: typed nodes with tier-1 figures + relationships)."""
    lines = [f"=== {dept.upper()} DEPARTMENT GRAPH (extracted by {model}) ==="]
    with driver.session(database=database) as s:
        nodes = s.run(
            "MATCH (n {_workspace_id:$w}) RETURN labels(n) AS l, properties(n) AS p",
            w=ws).data()
        rels = s.run(
            "MATCH (a {_workspace_id:$w})-[x]->(b {_workspace_id:$w}) "
            "RETURN coalesce(a.name,'?') AS s, type(x) AS t, "
            "coalesce(b.name,'?') AS o LIMIT 80", w=ws).data()
    for r in nodes:
        labs = [x for x in (r["l"] or []) if x not in _INFRA]
        if not labs:
            continue
        p = r["p"] or {}
        nm = p.get("name") or ""
        bits = [f"{k}={p[k]}" for k in
                ("value", "period", "basis", "segment", "amount") if p.get(k)]
        lines.append(f"- ({'/'.join(labs)}) {nm}" + (f" [{', '.join(bits)}]" if bits else ""))
    if rels:
        lines.append("--- relationships ---")
        lines.extend(f"- {r['s']} -{r['t']}-> {r['o']}" for r in rels)
    return "\n".join(lines)


def gold_context(case_id: str, master: dict, dept_uri: dict) -> str:
    """Consolidated golden-record context: anchor entity + surviving facts with
    confidence + physical-instance provenance + open quarantines."""
    gid = master["case_anchor"].get(case_id)
    if gid is None:
        return ""   # no golden record for this case — honest abstention
    g = next((x for x in master["golden_entities"] if x["golden_id"] == gid), None)
    if g is None:
        return ""
    lines = [
        "=== GOLDEN RECORD (MDM-consolidated master; survivorship "
        f"ruleset v{master['ruleset_version']}) ===",
        f"Entity: {g['name']}  (aliases: {', '.join(g['aliases'])}; "
        f"extracted independently by {g['model_count']}/3 department models)",
        "--- consolidated facts (value [period] — confidence, sources) ---",
    ]
    for f in master["golden_facts"]:
        if f["golden_id"] != gid:
            continue
        srcs = "; ".join(
            f"{c['source']}@{dept_uri.get(c['source'].split('/')[0], '?')}"
            for c in f["contributing"])
        lines.append(
            f"- {f['metric_raw']} [{f['period']}] = {f['value_raw']} "
            f"(confidence {f['confidence']}, agreement {f['agreement_count']}/"
            f"{f['sources_reporting']}; sources: {srcs})")
    quarantines = [t for t in master["steward_tasks"] if t["golden_id"] == gid]
    if quarantines:
        lines.append("--- UNRESOLVED (steward queue — departments disagree; "
                     "do not treat as fact) ---")
        for t in quarantines:
            vals = "; ".join(f"{c['source']}: {c['value']}" for c in t["contributing"])
            lines.append(f"- {t['metric_raw']} [{t['period']}]: {vals}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

def run_lane(*, lane: str, case: dict, instances, drivers, master, dept_uri,
             client, spec) -> dict:
    t0 = time.perf_counter()
    if lane.startswith("silo-"):
        dept = lane.split("-", 1)[1]
        inst = next(i for i in instances if i.dept == dept)
        ctx = silo_context(drivers[dept], inst.database,
                           f"mdm-{dept}-{case['case_id']}", dept, inst.model)
    elif lane == "federation":
        parts = [silo_context(drivers[i.dept], i.database,
                              f"mdm-{i.dept}-{case['case_id']}", i.dept, i.model)
                 for i in instances]
        ctx = ("=== LIVE FEDERATION across 3 department instances "
               "(raw union — values may conflict between departments) ===\n\n"
               + "\n\n".join(parts))
    elif lane == "gold":
        ctx = gold_context(case["case_id"], master, dept_uri)
    else:
        raise ValueError(lane)
    retrieval_ms = round((time.perf_counter() - t0) * 1000, 1)

    answer, ans_err = "", ""
    answer_ms = 0.0
    if not ctx.strip():
        answer = ABSTAIN_MARK    # empty context = abstain by contract
    else:
        t1 = time.perf_counter()
        try:
            answer = llm_io.chat_complete(
                client=client, model=spec.model, system=_ANSWER_SYSTEM,
                user=f"Question: {case['query']}\n\n{ctx}",
                temperature=0.0, label=lane, max_attempts=3, spec=spec)
        except Exception as exc:  # noqa: BLE001 — recorded, never imputed (§20.2)
            ans_err = f"{type(exc).__name__}: {exc}"
        answer_ms = round((time.perf_counter() - t1) * 1000, 1)

    m = evaluate_answer(case["expected_answer"], answer)
    return {
        "case_id": case["case_id"], "slice": case["slice"], "lane": lane,
        "query": case["query"], "expected_answer": case["expected_answer"],
        "answer": answer, "evaluation": m,
        "abstain": (ABSTAIN_MARK in (answer or "").lower()),
        "retrieval_ms": retrieval_ms, "answer_ms": answer_ms,
        "context_chars": len(ctx), "llm": f"mara/{spec.model}", "error": ans_err,
    }


# ---------------------------------------------------------------------------
# Aggregation + pre-registered verdicts
# ---------------------------------------------------------------------------

def aggregate(records: list[dict]) -> dict:
    by_lane: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_lane[r["lane"]].append(r)
    out = {}
    for lane, rs in sorted(by_lane.items()):
        ov = [r["evaluation"]["number_overlap_ratio"] for r in rs]
        out[lane] = {
            "n": len(rs),
            "number_overlap_mean": round(sum(ov) / len(ov), 3) if ov else 0.0,
            "contains_rate": round(sum(1 for r in rs if r["evaluation"]["contains_match"]) / len(rs), 3),
            "abstain_rate": round(sum(1 for r in rs if r["abstain"]) / len(rs), 3),
            "retrieval_ms_mean": round(sum(r["retrieval_ms"] for r in rs) / len(rs), 1),
            "answer_ms_mean": round(sum(r["answer_ms"] for r in rs) / len(rs), 1),
            "context_chars_mean": int(sum(r["context_chars"] for r in rs) / len(rs)),
            "errors": sum(1 for r in rs if r["error"]),
        }
    return out


def verdicts(agg: dict) -> dict:
    silos = {k: v for k, v in agg.items() if k.startswith("silo-")}
    best_silo = max(silos.values(), key=lambda v: v["number_overlap_mean"])
    gold, fed = agg.get("gold", {}), agg.get("federation", {})
    return {
        "H-FED1_gold_ge_best_silo": {
            "expected": "gold.overlap >= best silo overlap",
            "gold": gold.get("number_overlap_mean"),
            "best_silo": best_silo["number_overlap_mean"],
            "verdict": "SUPPORTED" if gold.get("number_overlap_mean", 0)
                       >= best_silo["number_overlap_mean"] else "REJECTED",
        },
        "H-FED2_union_coverage": {
            "expected": "gold & federation abstain <= every silo abstain",
            "gold_abstain": gold.get("abstain_rate"),
            "federation_abstain": fed.get("abstain_rate"),
            "silo_abstains": {k: v["abstain_rate"] for k, v in silos.items()},
            "verdict": "SUPPORTED" if (
                gold.get("abstain_rate", 1) <= min(v["abstain_rate"] for v in silos.values())
                and fed.get("abstain_rate", 1) <= min(v["abstain_rate"] for v in silos.values())
            ) else "REJECTED",
        },
        "H-FED3_consolidation_amortizes": {
            "expected": "federation overlap within 0.05 of gold; federation "
                        "context+retrieval cost > gold",
            "delta_overlap": round((fed.get("number_overlap_mean", 0)
                                    - gold.get("number_overlap_mean", 0)), 3),
            "context_ratio_fed_over_gold": round(
                fed.get("context_chars_mean", 0) / max(gold.get("context_chars_mean", 1), 1), 2),
            "retrieval_ratio_fed_over_gold": round(
                fed.get("retrieval_ms_mean", 0) / max(gold.get("retrieval_ms_mean", 1), 1), 2),
            "verdict": "SUPPORTED" if (
                abs(fed.get("number_overlap_mean", 0) - gold.get("number_overlap_mean", 0)) <= 0.05
                and fed.get("context_chars_mean", 0) > gold.get("context_chars_mean", 0)
            ) else "REJECTED",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="mara/DeepSeek-V3.1")
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bc.set_global_determinism(42)
    extractor = _load_extractor_module()
    cases = extractor.load_cases(n_per_slice=4, seed=42)
    if args.limit_cases:
        cases = cases[: args.limit_cases]

    lanes = ["silo-risk", "silo-research", "silo-compliance", "federation", "gold"]
    print(f"== plan: {len(cases)} cases × {len(lanes)} lanes = "
          f"{len(cases) * len(lanes)} answer calls (PAID: {args.llm}) ==")
    if args.dry_run:
        return 0

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
    with open((out_dir / "master_artifact.json"), "r", encoding="utf-8") as f:
        master = json.load(f)
    instances = federation.load_instances(MDM_ROOT / "config" / "instances.yaml")
    dept_uri = {i.dept: i.uri for i in instances}
    auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))

    from neo4j import GraphDatabase
    drivers = {i.dept: GraphDatabase.driver(i.uri, auth=auth) for i in instances}

    spec = llm_io.parse_llm_spec(args.llm)
    client = llm_io.make_chat_client(spec)

    out_partial = out_dir / "benchmark_partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    total = len(cases) * len(lanes)
    i = 0
    try:
        for case in cases:
            for lane in lanes:
                i += 1
                partial = out_partial / f"{lane}_{case['case_id']}.json"
                if args.resume and partial.is_file():
                    with open(partial, "r", encoding="utf-8") as f:
                        rec = json.load(f)
                    if rec.get("llm") == f"mara/{spec.model}" and not rec.get("error"):
                        print(f">>> [{i}/{total}] {lane} {case['case_id']} — SKIP (resume)")
                        records.append(rec)
                        continue
                print(f">>> [{i}/{total}] {lane} {case['case_id']}")
                rec = run_lane(lane=lane, case=case, instances=instances,
                               drivers=drivers, master=master, dept_uri=dept_uri,
                               client=client, spec=spec)
                bc.atomic_write_json(partial, rec)
                ev = rec["evaluation"]
                print(f"    overlap={ev['number_overlap_ratio']:.2f} "
                      f"abstain={rec['abstain']} ctx={rec['context_chars']} "
                      f"retr={rec['retrieval_ms']}ms ans={rec['answer_ms']}ms"
                      + (f"  ERR {rec['error']}" if rec["error"] else ""))
                records.append(rec)
    finally:
        for d in drivers.values():
            d.close()

    agg = aggregate(records)
    verd = verdicts(agg)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix, "llm": args.llm, "seed": 42,
        "lanes": lanes, "n_cases": len(cases),
        "attempted": len(records),
        "errors": sum(1 for r in records if r["error"]),
        "ruleset_version": master["ruleset_version"],
        "instances": [{"dept": i.dept, "uri": i.uri, "model": i.model}
                      for i in instances],
        "pre_registered_hypotheses": verd,
        "aggregate_by_lane": agg,
        "records": records,
    }
    agg_path = out_dir / "benchmark_aggregate.json"
    bc.atomic_write_json(agg_path, payload)

    print(f"\n== wrote {agg_path.relative_to(ROOT)} ==")
    print(f"\n{'lane':<16} | {'n':>2} | overlap | contains | abstain | "
          f"retr ms | ans ms | ctx chars | err")
    print("-" * 90)
    for lane, v in agg.items():
        print(f"{lane:<16} | {v['n']:>2} | {v['number_overlap_mean']:.3f}   | "
              f"{v['contains_rate']:.2f}     | {v['abstain_rate']:.2f}    | "
              f"{v['retrieval_ms_mean']:>7.1f} | {v['answer_ms_mean']:>6.0f} | "
              f"{v['context_chars_mean']:>9} | {v['errors']}")
    print("\n== pre-registered hypothesis verdicts (disconfirming reported too) ==")
    for h, d in verd.items():
        print(f"  {h}: {d['verdict']}")
        for k, val in d.items():
            if k not in ("verdict",):
                print(f"      {k}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
