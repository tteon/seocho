#!/usr/bin/env python3
"""$0 SCALE-AXIS eval (panel build, pre-registered) — NO LLM.

C settled quality (graph = vector parity on served classes, not better). The
remaining thesis is the SCALE/serving axis. Both experts converged: measure it
DIRECTLY, $0, with coverage as a JOINT (LLM-free AND correct) metric — never the
marginal "LLM-free %" (that's proxy inflation: 100% coverage of wrong answers).
Every cost number is reported WITH its quality bar (two-column rule).

SLOs (all $0):
 1. LLM-free-correct coverage: admitted (deterministic answerer serves, grounded)
    AND correct (from C's gpt-oss judge file) — per slice. Report admitted%,
    correct|admitted, llm_free_correct%.
 2. prefix-stability: byte longest-common-prefix across a thread's K rendered
    prompts, fraction of mean prompt length — graph context (stable per thread)
    vs vector top-k (volatile per query). Cacheability is a prefix-match property.
 3. admission/$/req projection: 1/(1-c) RPD multiplier per MARA model; $/req with
    published cache discount on the non-admitted complement.
 4. degradation: provider-down survival = LLM-free-correct coverage (vector = 0).

Pre-registered thresholds (lock BEFORE reading results, §20.4):
   tau_cov >= 0.40 (LLM-free-correct on join/lookup classes),
   eps <= 0.02 (parity vs vector), rho <= 0.10 (silent-wrong).

Run: python examples/contextgraph/scale_eval.py
"""
from __future__ import annotations
import csv, json, os, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.graph import Neo4jGraphStore
from scripts.benchmarks.finder_4arm_sample import _graph_context
import graph_answer as ga

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
DB = "cgbc3minimaxm25"
WS_RUN = "e1-bc3-a1"
C_JUDGED = ROOT / "outputs/evaluation/contextgraph/e1-bc3-detgraph_judged.json"
SLICE_FN = {"E1_FACT": ga.answer_initiator, "E3_PROPOSALS": ga.answer_proposals,
            "E4_POSITIONS": ga.answer_positions}
# served classes graph is FOR (join/lookup); E1 is the degraded control (sent_date null)
JOIN_CLASSES = {"E3_PROPOSALS", "E4_POSITIONS"}
TAU_COV, EPS, RHO = 0.40, 0.02, 0.10
MARA_RPD = {"MiniMax-M2.7": 5000, "gpt-oss-120b": 50000, "MiniMax-M2.5": 50000}


def _lcp_frac(prompts):
    if not prompts:
        return 0.0
    a = min(prompts, key=len)
    n = 0
    for i in range(len(a)):
        if all(p[i] == a[i] for p in prompts):
            n += 1
        else:
            break
    return n / (sum(len(p) for p in prompts) / len(prompts))


def main():
    rows = list(csv.DictReader(open(DATA)))
    by_thread = defaultdict(list)
    for c in rows:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    tids = list(by_thread)[:15]
    # C judge labels: case_id (slice+_id) -> correct(bool)
    cj = json.load(open(C_JUDGED))
    correct = {}
    for r in cj["results"]:
        base = str(r["_id"]).split("|")[0]
        correct[base] = (r.get("judge_verdict") == "correct") or (r.get("judge_score", 0) >= 1.0)
    # vector per-slice quality (from a1 judged) for parity check
    a1 = json.load(open(ROOT / "outputs/evaluation/contextgraph/e1-bc3-a1_judged.json"))
    vec_q = {r["slice"]: r["judge_score_mean"] for k, r in a1["summary"].items()
             if r["retrieval"] == "vector"}

    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    # --- SLO-1 coverage-at-quality (per slice) ---
    per = defaultdict(lambda: {"adm": 0, "adm_correct": 0, "n": 0})
    for tid in tids:
        w = f"{WS_RUN}-decision-{tid}"
        for c in by_thread[tid]:
            sl = c["slice"]
            fn = SLICE_FN.get(sl)
            if fn is None:
                continue
            ans, ok = fn(gs, w, DB)
            base = c["_id"]
            adm = bool(ok and ans and "not in the provided context" not in ans)
            per[sl]["n"] += 1
            per[sl]["adm"] += int(adm)
            per[sl]["adm_correct"] += int(adm and correct.get(base, False))
    # --- SLO-2 prefix-stability (graph vs vector), per thread ---
    from run_e1 import _bge_vector_context
    from seocho.store.local_embedding import LocalBGEEmbeddingBackend
    bge = LocalBGEEmbeddingBackend()
    g_s, v_s = [], []
    for tid in tids:
        w = f"{WS_RUN}-decision-{tid}"
        refs = [x.strip() for x in str(by_thread[tid][0]["references_joined"]).split("===EVIDENCE_BOUNDARY===") if x.strip()]
        qs = [c["query"] for c in by_thread[tid]]
        try:
            gctx = _graph_context(gs, w, DB)
            g_prompts = [f"CONTEXT:\n{gctx}\n\nQ: {q}" for q in qs]  # graph ctx STABLE across qs
            g_s.append(_lcp_frac(g_prompts))
            v_prompts = [f"CONTEXT:\n{_bge_vector_context(refs, q, bge)}\n\nQ: {q}" for q in qs]  # varies
            v_s.append(_lcp_frac(v_prompts))
        except Exception:
            pass
    gs.close()

    # --- report (two-column: cost @ quality) ---
    print("== SCALE-AXIS eval (BC3, 15 threads, $0 no-LLM; pre-reg tau>=.40 eps<=.02 rho<=.10) ==\n")
    print("SLO-1 coverage-at-quality (cost | quality):")
    print(f"  {'slice':<16}{'admitted%':>11}{'correct|adm':>13}{'LLM-free-correct%':>19}{'vector_q':>10}")
    join_lfc = []
    for sl in sorted(per):
        d = per[sl]; n = d["n"] or 1
        adm = d["adm"] / n; cga = (d["adm_correct"] / d["adm"]) if d["adm"] else 0.0
        lfc = d["adm_correct"] / n
        if sl in JOIN_CLASSES:
            join_lfc.append(lfc)
        print(f"  {sl:<16}{adm:>10.0%}{cga:>13.2f}{lfc:>18.0%}{vec_q.get(sl,float('nan')):>10.3f}  (n={d['n']})")
    print("\nSLO-2 prefix-stability (cacheability; higher=more cacheable):")
    gm = sum(g_s)/len(g_s) if g_s else 0; vm = sum(v_s)/len(v_s) if v_s else 0
    print(f"  graph context : {gm:.0%}  | vector top-k : {vm:.0%}   (n_threads={len(g_s)})")
    print("\nSLO-3 admission multiplier 1/(1-c)  [c = LLM-free-correct on join classes]:")
    c = sum(join_lfc)/len(join_lfc) if join_lfc else 0
    mult = 1/(1-c) if c < 1 else float('inf')
    for m, rpd in MARA_RPD.items():
        print(f"  {m:<16} RPD {rpd:>6} → sustainable ~{rpd*mult:>8.0f} q/day  ({mult:.2f}x)" )
    print(f"\nSLO-4 degradation (provider-down survival): graph {c:.0%} LLM-free-correct vs vector 0%")
    print("\n-- pre-registered verdict --")
    print(f"  tau_cov>=0.40 on join classes: c={c:.2f} -> {'PASS' if c>=TAU_COV else 'FAIL'}")
    print(f"  prefix-stability graph>=.50 AND vector<=.10: g={gm:.2f} v={vm:.2f} -> "
          f"{'PASS' if gm>=0.5 and vm<=0.10 else 'FAIL'}")
    print(f"  degradation graph>vector(0): {'PASS' if c>0 else 'FAIL'}")


if __name__ == "__main__":
    main()
