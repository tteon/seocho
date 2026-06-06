#!/usr/bin/env python3
"""LLM-free position-polarity AGGREGATION over the governed HOLDS_POSITION edge.

The Answerability Gate end-to-end demo (step 3). The 'decision' arm left
E4_POSITIONS UNCOVERED (no governed opinion relation; answers came from
prompt-smuggled SUPPORTS/OPPOSES → silent-wrong). The 'position' arm adds the
MINIMAL governed edge (:Person)-[:HOLDS_POSITION {polarity, source_quote}]->(:Topic)
— which flips the gate to CERTIFIED and makes a query class servable that vector
structurally cannot: a per-topic FOR/AGAINST/NEUTRAL tally ACROSS people, each
claim carrying a verbatim source quote. Deterministic, $0, no LLM.

This is the class the type was declared for (per the ontologist's rule: declare a
relation only if it backs a JOIN/aggregation, never single-opinion prose). For a
single "what does X think about T" lookup, route to vector instead.

Run: python examples/contextgraph/position_aggregation.py --db cgbc3pos \
        --ws-prefix e1-bc3-pos-position-
"""
from __future__ import annotations
import argparse, logging, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from seocho.store.graph import Neo4jGraphStore


def aggregate(gs, ws, db):
    """Per-topic polarity distribution across people + provenance (LLM-free)."""
    return gs.query(
        "MATCH (p:Person {_workspace_id:$w})-[r:HOLDS_POSITION]->(t:Topic {_workspace_id:$w}) "
        "WITH t.name AS topic, r.polarity AS polarity, "
        "     collect(DISTINCT p.name) AS people, "
        "     collect({person:p.name, quote:r.source_quote})[0..3] AS evidence, count(*) AS n "
        "WITH topic, collect({polarity:polarity, people:people, n:n, evidence:evidence}) AS dist, "
        "     sum(n) AS total, count(DISTINCT polarity) AS n_pol "
        "WHERE total >= 2 "
        "RETURN topic, total, n_pol, dist ORDER BY n_pol DESC, total DESC",
        params={"w": ws}, database=db) or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3pos")
    ap.add_argument("--ws-prefix", default="e1-bc3-pos-position-")
    args = ap.parse_args()
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        wss = [r["w"] for r in gs.query(
            "MATCH (n) WHERE n._workspace_id STARTS WITH $p RETURN DISTINCT n._workspace_id AS w ORDER BY w",
            params={"p": args.ws_prefix}, database=args.db) or []]
        print("== LLM-free position-polarity aggregation (CERTIFIED via HOLDS_POSITION) ==")
        print("   vector cannot produce a per-topic cross-person FOR/AGAINST tally with provenance.\n")
        contested = 0
        for w in wss:
            rows = aggregate(gs, w, args.db)
            if not rows:
                continue
            print(f"-- thread {w.split('-')[-1]} --")
            for r in rows:
                tally = " ".join(f"{d['polarity']}={d['n']}" for d in r["dist"])
                mark = "  <-- CONTESTED (mixed polarity)" if r["n_pol"] > 1 else ""
                print(f"  • {r['topic']!r}  (n={r['total']}) {tally}{mark}")
                for d in r["dist"]:
                    ev = d["evidence"][0] if d["evidence"] else {}
                    who = ", ".join(d["people"])
                    print(f"      {d['polarity']}: {who}")
                    if ev.get("quote"):
                        print(f"        “{str(ev['quote'])[:90]}”  ({ev.get('person')})")
                if r["n_pol"] > 1:
                    contested += 1
        print(f"\ncontested topics surfaced (mixed FOR/AGAINST across people): {contested}")
        print("each cell is grounded in a verbatim source_quote → auditable, $0, no LLM.")
    finally:
        gs.close()


if __name__ == "__main__":
    main()
