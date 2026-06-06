#!/usr/bin/env python3
"""Panel build C — accuracy of the Tier-1 deterministic graph answerer.

Hand-built structured golds for narrative decision threads are subjective (a bad
gold is worse than none, §20). Instead we score the deterministic answerer's
OUTPUT with the SAME decision judge used for vector/approach1 — directly
comparable accuracy: does the LLM-FREE, grounded graph answer match vector's
quality on the classes the graph serves (E3_PROPOSALS who-proposed, E4_POSITIONS
who-for/against)? If yes, that's the win — LLM-free + verifiable + comparable.

Reuses graph_answer.py (deterministic, $0) to produce answers; writes finder-
partial JSON (retrieval=graph, arm=deterministic) → judge with mara/gpt-oss-120b
(decision rubric). Compare to e1-bc3-a1_judged.json (vector + approach1 graph@dec,
same gpt-oss judge, same 15 threads).

Run: python examples/contextgraph/run_graph_answer_eval.py --threads 15
"""
from __future__ import annotations
import argparse, csv, json, os, sys
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
import graph_answer as ga

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
DB = "cgbc3minimaxm25"
WS_RUN = "e1-bc3-a1"  # the (merged) approach1 graphs

# slice -> deterministic answerer fn (graph's serveable classes)
SLICE_FN = {
    "E1_FACT": ga.answer_initiator,
    "E3_PROPOSALS": ga.answer_proposals,
    "E4_POSITIONS": ga.answer_positions,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=15)
    ap.add_argument("--run", default="e1-bc3-detgraph")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(DATA)))
    by_thread = defaultdict(list)
    for c in rows:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    tids = list(by_thread)[: args.threads]
    out_dir = ROOT / "outputs" / "evaluation" / "contextgraph" / args.run / "partial"
    out_dir.mkdir(parents=True, exist_ok=True)
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    n = 0
    try:
        for tid in tids:
            w = f"{WS_RUN}-decision-{tid}"
            for c in by_thread[tid]:
                fn = SLICE_FN.get(c["slice"])
                if fn is None:
                    continue
                ans, ok = fn(gs, w, DB)
                rec = {"_id": f"{c['_id']}|graph|deterministic", "slice": c["slice"],
                       "category": "Decision", "query": c["query"], "expected_answer": c["answer"],
                       "answer": ans, "retrieval": "graph", "mode": "graph", "arm": "deterministic",
                       "verifiable": ok, "model": "graph/deterministic-LLM-free",
                       "evaluation": {"number_overlap_ratio": 0.0}}
                (out_dir / f"{c['slice']}_{c['_id']}_detgraph.json".replace("#", "_")).write_text(
                    json.dumps(rec, default=str))
                n += 1
    finally:
        gs.close()
    print(f"wrote {n} deterministic-answer partials -> {out_dir}")
    print("judge: python scripts/benchmarks/finder_judge.py --judge-domain decision "
          f"--judge-llms mara/gpt-oss-120b --inputs 'outputs/evaluation/contextgraph/{args.run}/partial/*.json' "
          f"--out outputs/evaluation/contextgraph/{args.run}_judged.json")


if __name__ == "__main__":
    main()
