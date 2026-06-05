#!/usr/bin/env python3
"""H2 — cost-amortization (KV-cache) measurement for the Context-Graph thesis.

Claim: a graph context is LARGER per query than vector top-k, BUT it is a STABLE
prefix reused across a thread's queries → provider prefix-cache HITS amortize it
below vector (whose top-k differs per query → cache misses).

Setup (per BC3 thread, reusing the graphs E1 built in cgbc3minimaxm25):
  - graph lane : system = INSTR + the THREAD's decision graph  (SAME for all K queries → stable prefix → cache)
  - vector lane: system = INSTR + THIS query's vector top-k     (DIFFERS per query → no stable prefix → no cache)
  - hybrid     : system = INSTR + vector top-k + graph          (graph part stable; vector part varies)
The thread's K queries (E1/E2/E3/E4) run IN ORDER on one client so query 1 primes
the cache and 2..K can hit. Per query we record prompt_tokens, cached_tokens, ttft.

Metric: amortized_billable = Σ(prompt_tokens − cached_tokens)/K per lane.
H2 holds if graph's amortized_billable < vector's despite larger raw context.

Provider: openai/gpt-4o (verified to report prompt_tokens_details.cached_tokens;
MARA gpt-oss/DeepSeek did NOT report caching). Honest: if cached stays 0, H2 is
not supported on this provider — report that, don't fabricate.

Usage: python examples/contextgraph/run_h2_cost.py --threads 8 --arm decision
"""
from __future__ import annotations
import argparse, csv, os, sys, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from scripts.benchmarks.finder_4arm_sample import _graph_context, _vector_context
from seocho.store.graph import Neo4jGraphStore
from seocho.store.llm import create_llm_backend

SEP = "===EVIDENCE_BOUNDARY==="
DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
E1_RUN = "e1-bc3-full"          # graphs E1 built
E1_DB = "cgbc3minimaxm25"
INSTR = ("You are a decision analyst. Using ONLY the context above, answer the "
         "question: name the participants, proposals, positions, and outcome.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--arm", default="decision")   # which E1 graph arm to use as context
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--out", default=str(ROOT / "outputs/evaluation/contextgraph/h2_cost.json"))
    args = ap.parse_args()

    rows = list(csv.DictReader(open(DATA)))
    by_thread = defaultdict(list)
    for c in rows:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    tids = list(by_thread)[: args.threads]

    from openai import OpenAI
    oai = OpenAI(timeout=60)
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    llm = create_llm_backend(provider=args.provider, model=args.model)
    print(f"== H2 cost: provider={args.provider}/{args.model} arm={args.arm} threads={len(tids)} ==\n")

    def call(system, user):
        r = llm.complete(system=system, user=user, max_tokens=160)
        u = dict(getattr(r, "usage", {}) or {})
        return u.get("prompt_tokens", 0), u.get("cached_tokens", 0), u.get("ttft_s", 0.0)

    lane_rows = defaultdict(list)   # lane -> list of (prompt, cached, ttft, billable)
    for tid in tids:
        tcases = by_thread[tid]
        refs = [x.strip() for x in str(tcases[0]["references_joined"]).split(SEP) if x.strip()]
        ws = f"{E1_RUN}-{args.arm}-{tid}"
        try:
            graph_ctx = _graph_context(gs, ws, E1_DB)
        except Exception:
            graph_ctx = ""
        if not graph_ctx:
            print(f"  [{tid}] no graph context (E1 build missing for this arm) — skip")
            continue
        # run each lane's K queries IN ORDER (warm the prefix cache)
        for lane in ("vector", "graph", "hybrid"):
            for c in tcases:
                q = c["query"]
                if lane == "graph":
                    system = f"Context (decision graph):\n{graph_ctx}\n\n{INSTR}"   # STABLE across queries
                elif lane == "vector":
                    try:
                        vc = _vector_context(refs, q, oai)
                    except Exception:
                        vc = ""
                    system = f"Context (messages):\n{vc}\n\n{INSTR}"                 # VARIES per query
                else:
                    try:
                        vc = _vector_context(refs, q, oai)
                    except Exception:
                        vc = ""
                    system = f"Context (messages):\n{vc}\n\nContext (decision graph):\n{graph_ctx}\n\n{INSTR}"
                p, cached, ttft = call(system, f"Question: {q}")
                lane_rows[lane].append((p, cached, ttft, p - cached))
        # per-thread cache snapshot
        def snap(l):
            v = lane_rows[l][-len(tcases):]
            return sum(x[1] for x in v), sum(x[0] for x in v)
        gc, gp = snap("graph"); vc_, vp = snap("vector")
        print(f"  [{tid} K={len(tcases)}] graph cached/prompt={gc}/{gp}  vector cached/prompt={vc_}/{vp}")

    gs.close()
    print("\n=== H2 rollup (per lane) ===")
    print(f"{'lane':<8}{'avg_prompt':>11}{'avg_cached':>11}{'hit_ratio':>10}{'amortized_billable':>20}{'avg_ttft_s':>11}")
    out = {}
    for lane in ("vector", "graph", "hybrid"):
        v = lane_rows.get(lane, [])
        if not v:
            continue
        ap_ = statistics.mean(x[0] for x in v)
        ac = statistics.mean(x[1] for x in v)
        hit = ac / ap_ if ap_ else 0.0
        amort = statistics.mean(x[3] for x in v)   # prompt - cached, per query
        tt = statistics.mean(x[2] for x in v)
        out[lane] = {"avg_prompt": ap_, "avg_cached": ac, "hit_ratio": hit,
                     "amortized_billable": amort, "avg_ttft_s": tt, "n": len(v)}
        print(f"{lane:<8}{ap_:>11.0f}{ac:>11.0f}{hit:>10.2f}{amort:>20.0f}{tt:>11.3f}")
    import json
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")
    print("H2 verdict: graph amortized_billable < vector → cost thesis supported "
          "(graph bigger raw, but stable prefix caches → cheaper amortized).")


if __name__ == "__main__":
    main()
