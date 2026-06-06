#!/usr/bin/env python3
"""$0 stage-local EXTRACTION metric — tuple precision/recall/F1 (NO LLM, NO judge).

The arbiter is unstable (kappa 0.2-0.5), so prompt optimization is measured HERE
instead: how well does an arm's extracted graph recover the gold tuples? This
reads PROPOSES / SUPPORTS / OPPOSES from the graph and matches them against the
M2.7-drafted gold tuples (gold_tuples_m27.json) with canonicalization-aware
matching. One hop from the prompt, deterministic, free — the right substrate for
single-variable prompt ablations (a1 vs a2 vs ...).

Reports PRECISION and RECALL SEPARATELY per slice (never F1 alone — an arm that
extracts fewer/safer tuples games F1; gate on recall non-regression — arch-review).

Matching (conservative): name = ordered token-prefix OR equal token-set
(Brian ⊑ Brian McBride); proposal = content-token Jaccard >= 0.5 OR subset;
E4 also requires direction match (FOR=SUPPORTS, AGAINST=OPPOSES).

Run: python examples/contextgraph/tuple_metric.py --db cgbc3minimaxm25 \
        --ws-prefix e1-bc3-a1-decision- --label a1
"""
from __future__ import annotations
import argparse, json, logging, os, re, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from seocho.store.graph import Neo4jGraphStore

GOLD = ROOT / "outputs/evaluation/contextgraph/gold_tuples_m27.json"
_STOP = {"the", "a", "an", "of", "to", "for", "at", "in", "on", "and", "or", "be",
         "we", "i", "is", "it", "this", "that", "s", "1", "2"}


def _toks(s):
    return [t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _STOP]


def _name_match(a, b):
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    short, lng = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return lng[:len(short)] == short  # ordered prefix (Brian ⊑ Brian McBride)


def _prop_match(a, b):
    sa, sb = set(_toks(a)), set(_toks(b))
    if not sa or not sb:
        return False
    if sa <= sb or sb <= sa:
        return True
    j = len(sa & sb) / len(sa | sb)
    return j >= 0.5


def _graph_tuples(gs, ws, db):
    """Extract (proposer,proposal) and (person,direction,proposal) from the arm graph."""
    props = gs.query(
        "MATCH (p:Person {_workspace_id:$w})-[:PROPOSES]->(pr:Proposal {_workspace_id:$w}) "
        "RETURN p.name AS person, pr.name AS prop", params={"w": ws}, database=db) or []
    stances = gs.query(
        "MATCH (p:Person {_workspace_id:$w})-[r]->(pr:Proposal {_workspace_id:$w}) "
        "WHERE type(r) IN ['SUPPORTS','OPPOSES'] "
        "RETURN p.name AS person, type(r) AS t, pr.name AS prop", params={"w": ws}, database=db) or []
    e3 = [(r["person"], r["prop"]) for r in props if r.get("person") and r.get("prop")]
    e4 = [(r["person"], "FOR" if r["t"] == "SUPPORTS" else "AGAINST", r["prop"])
          for r in stances if r.get("person") and r.get("prop")]
    return e3, e4


def _match_e3(gold, ext):
    return any(_name_match(gold["proposer"], e[0]) and _prop_match(gold["proposal_gist"], e[1]) for e in ext)


def _match_e4(gold, ext):
    return any(_name_match(gold["person"], e[0]) and gold["direction"] == e[1]
               and _prop_match(gold["proposal_gist"], e[2]) for e in ext)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3minimaxm25")
    ap.add_argument("--ws-prefix", default="e1-bc3-a1-decision-")
    ap.add_argument("--label", default="a1")
    args = ap.parse_args()
    gold = json.loads(GOLD.read_text())
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    agg = defaultdict(lambda: {"g": 0, "e": 0, "g_hit": 0, "e_hit": 0})
    try:
        for key, rec in gold.items():
            facts = rec.get("facts")
            if not facts:
                continue
            sl = rec["slice"]
            tid = str(rec["_id"]).split("#")[0]
            ws = f"{args.ws_prefix}{tid}"
            e3, e4 = _graph_tuples(gs, ws, args.db)
            ext = e3 if sl == "E3_PROPOSALS" else e4
            mfn = _match_e3 if sl == "E3_PROPOSALS" else _match_e4
            d = agg[sl]
            for gf in facts:
                d["g"] += 1
                if mfn(gf, ext):
                    d["g_hit"] += 1
            # precision: extracted tuples that hit some gold fact
            for e in ext:
                d["e"] += 1
                ge = {"proposer": e[0], "proposal_gist": e[-1]} if sl == "E3_PROPOSALS" else \
                     {"person": e[0], "direction": e[1], "proposal_gist": e[2]}
                if any((_match_e3 if sl == "E3_PROPOSALS" else _match_e4)(gf2, [e]) for gf2 in facts):
                    d["e_hit"] += 1
    finally:
        gs.close()
    print(f"== tuple-F1 (arm={args.label}, db={args.db}) — $0, gold=M2.7-drafted (unvalidated) ==")
    print(f"  {'slice':<16}{'recall':>9}{'precision':>11}{'F1':>8}{'gold':>6}{'extracted':>11}")
    for sl in sorted(agg):
        d = agg[sl]
        rec = d["g_hit"] / d["g"] if d["g"] else 0.0
        prec = d["e_hit"] / d["e"] if d["e"] else 0.0
        f1 = 2 * rec * prec / (rec + prec) if (rec + prec) else 0.0
        print(f"  {sl:<16}{rec:>8.0%}{prec:>11.0%}{f1:>8.2f}{d['g']:>6}{d['e']:>11}")


if __name__ == "__main__":
    main()
