#!/usr/bin/env python3
"""$0 graph-STRENGTH eval — measure what a graph is actually FOR (panel build #1).

The content-vs-context study scored graph on prose-QA (vector's home turf) and it
lost. Both experts: that's the wrong eval. A graph's real job is the operations
single-passage QA can't measure — and at 100K-concurrent / 5K-RPD, the wins are
LLM-FREE deterministic answers + cacheable stable prefix. This profiler measures
those, with NO LLM (pure structural Cypher), per workspace:

  - LLM-FREE SERVING by query class (the Tier-1 admission-control SLO):
      LOOKUP   : message sender + sent_date deterministically retrievable
      JOIN     : multi-hop Person-PROPOSES->Proposal<-SUPPORTS/OPPOSES-Person
                 (a 2-hop join no single passage holds)
      AGGREGATE: deterministic counts (proposals / decisions / stances) per thread
  - PROVENANCE: fraction of decision-bearing nodes carrying source_quote
  - CACHE PREFIX: the per-workspace graph-context size = a STABLE cacheable prefix
                  (vector top-k varies per query → not cacheable). H2 substrate.

A query class is "graph-answerable (LLM-free)" iff the typed query returns >=1
structured row. % of workspaces answerable per class = the metric. This is what
SHOULD reward graph, and what we had NOT been running.

Run: python examples/contextgraph/graph_strength.py --db cgbc3minimaxm25 --ws-prefix e1-bc3-a1-decision-
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.graph import Neo4jGraphStore

_STANCE = ["SUPPORTS", "OPPOSES", "HAS_STANCE", "AGAINST", "FAVORS", "CONTRADICTS"]


def _n(gs, cy, w, db):
    try:
        r = gs.query(cy, params={"w": w}, database=db)
        return r[0]["c"] if r else 0
    except Exception:
        return 0


def probe(gs, w, db):
    """Return per-class deterministic answerability (bool) + provenance + prefix size."""
    out = {}
    # LOOKUP: a message with sender (SENT edge) AND a non-empty sent_date
    out["LOOKUP_who_when"] = _n(gs,
        "MATCH (p:Person {_workspace_id:$w})-[:SENT]->(m:EmailMessage {_workspace_id:$w}) "
        "WHERE m.sent_date IS NOT NULL AND m.sent_date<>'' RETURN count(*) AS c", w, db) > 0
    # JOIN: 2-hop — a proposer AND a (different-edge) stance-taker on the SAME proposal
    out["JOIN_proposal_stance"] = _n(gs,
        f"MATCH (pr:Person {{_workspace_id:$w}})-[:PROPOSES]->(p:Proposal {{_workspace_id:$w}}) "
        f"MATCH (st:Person {{_workspace_id:$w}})-[r]->(p) WHERE type(r) IN {_STANCE} "
        f"RETURN count(*) AS c", w, db) > 0
    # AGGREGATE: deterministic count of proposals (>=2 = a real aggregation target)
    out["AGG_proposal_count"] = _n(gs,
        "MATCH (p:Proposal {_workspace_id:$w}) RETURN count(p) AS c", w, db) >= 2
    return out


def provenance_frac(gs, w, db):
    tot = _n(gs, "MATCH (n {_workspace_id:$w}) WHERE labels(n)[0] IN ['Proposal','Decision','Stance'] RETURN count(n) AS c", w, db)
    if not tot:
        return None
    grounded = _n(gs, "MATCH (n {_workspace_id:$w}) WHERE labels(n)[0] IN ['Proposal','Decision','Stance'] "
                      "AND (n.source_quote IS NOT NULL AND n.source_quote<>'') RETURN count(n) AS c", w, db)
    return grounded / tot


def prefix_size(gs, w, db):
    # rel triples that would serialize into the (cacheable) graph prefix
    return _n(gs, "MATCH (a {_workspace_id:$w})-[r]->(b {_workspace_id:$w}) "
                  "WHERE NOT labels(a)[0] IN ['Document','DocumentVersion','Chunk','Section'] "
                  "RETURN count(r) AS c", w, db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3minimaxm25")
    ap.add_argument("--ws-prefix", default="e1-bc3-a1-decision-")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        wss = [r["w"] for r in gs.query(
            "MATCH (n) WHERE n._workspace_id STARTS WITH $p RETURN DISTINCT n._workspace_id AS w ORDER BY w",
            params={"p": args.ws_prefix}, database=args.db)]
        if args.limit:
            wss = wss[: args.limit]
        cls = defaultdict(int)
        prov = []; pref = []
        for w in wss:
            for k, ok in probe(gs, w, args.db).items():
                cls[k] += int(ok)
            f = provenance_frac(gs, w, args.db)
            if f is not None:
                prov.append(f)
            pref.append(prefix_size(gs, w, args.db))
        n = len(wss)
        print(f"== graph-strength: {n} workspaces ({args.ws_prefix}) — NO LLM ==\n")
        print("LLM-FREE DETERMINISTIC SERVING (% of workspaces answerable per query class):")
        for k in ["LOOKUP_who_when", "JOIN_proposal_stance", "AGG_proposal_count"]:
            print(f"  {k:<24} {cls[k]}/{n} ({cls[k]/n:.0%})")
        anyc = sum(1 for w in wss if any(probe(gs, w, args.db).values()))
        print(f"  ANY class answerable     {anyc}/{n} ({anyc/n:.0%})  <- Tier-1 LLM-free coverage")
        if prov:
            print(f"\nPROVENANCE (source_quote on decision nodes): mean {sum(prov)/len(prov):.0%}")
        if pref:
            import statistics
            print(f"CACHE PREFIX (typed rel triples per workspace = stable cacheable prefix): "
                  f"mean {statistics.mean(pref):.0f}, max {max(pref)}")
            print("  (vector top-k varies per query → NOT cacheable; this prefix is fixed per workspace → KV-cache substrate)")
    finally:
        gs.close()


if __name__ == "__main__":
    main()
