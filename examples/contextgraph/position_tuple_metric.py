#!/usr/bin/env python3
"""$0 accuracy for the position arm — HOLDS_POSITION tuple precision/recall/F1.

Bypasses the unstable arbiter (kappa 0.2-0.5): scores the position-arm graph's
extracted (:Person)-[:HOLDS_POSITION {polarity}]->(:Topic) tuples against the
M2.7-drafted gold positions (position_gold_m27.json). Matching (conservative):
person = ordered token-prefix; topic = content-token Jaccard >= 0.5 OR subset;
polarity = exact (FOR/AGAINST/NEUTRAL). Reports recall + precision SEPARATELY
(no F1-gaming). Gold is M2.7-drafted/unvalidated (B2) — disclosed.

Run: python examples/contextgraph/position_tuple_metric.py --db cgbc3pos15 \
        --ws-prefix e1-bc3-pos15-position-
"""
from __future__ import annotations
import argparse, json, logging, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from seocho.store.graph import Neo4jGraphStore

GOLD = ROOT / "outputs/evaluation/contextgraph/position_gold_m27.json"
_STOP = {"the", "a", "an", "of", "to", "for", "at", "in", "on", "and", "or", "be",
         "we", "i", "is", "it", "this", "that", "s"}


def _toks(s):
    return [t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _STOP]


def _name_match(a, b):
    # ORDER-INSENSITIVE token-set match: person names appear as "First Last",
    # "Last, First", "First M Last" across gold (M2.7) vs graph. Ordered-prefix
    # wrongly missed "Dickinson, Ian J" vs "Ian J Dickinson" (same person). Match
    # if token-sets are equal OR one is a subset of the other (First vs First-Last).
    sa, sb = set(_toks(a)), set(_toks(b))
    if not sa or not sb:
        return False
    return sa == sb or sa <= sb or sb <= sa


def _topic_match(a, b):
    sa, sb = set(_toks(a)), set(_toks(b))
    if not sa or not sb:
        return False
    if sa <= sb or sb <= sa:
        return True
    return len(sa & sb) / len(sa | sb) >= 0.5


def _pol(x):
    return str(x or "").strip().upper()


def _match(gold, ext):
    # ext: list of (person, polarity, topic)
    return any(_name_match(gold["person"], e[0]) and _pol(gold["polarity"]) == _pol(e[1])
               and _topic_match(gold["topic"], e[2]) for e in ext)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3pos15")
    ap.add_argument("--ws-prefix", default="e1-bc3-pos15-position-")
    args = ap.parse_args()
    gold = json.loads(GOLD.read_text())
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    g = e = g_hit = e_hit = n_cases = 0
    try:
        for key, rec in gold.items():
            facts = rec.get("facts")
            if not facts:
                continue
            tid = str(rec["_id"]).split("#")[0]
            ws = f"{args.ws_prefix}{tid}"
            rows = gs.query(
                "MATCH (p:Person {_workspace_id:$w})-[r:HOLDS_POSITION]->(t:Topic {_workspace_id:$w}) "
                "RETURN p.name AS person, r.polarity AS pol, t.name AS topic",
                params={"w": ws}, database=args.db) or []
            ext = [(r["person"], r["pol"], r["topic"]) for r in rows if r.get("person") and r.get("topic")]
            if not rows:
                continue  # workspace not built (skip, don't penalize unbuilt)
            n_cases += 1
            for gf in facts:
                g += 1
                if _match(gf, ext):
                    g_hit += 1
            for et in ext:
                e += 1
                ge = {"person": et[0], "polarity": et[1], "topic": et[2]}
                if any(_match(gf2, [et]) for gf2 in facts):
                    e_hit += 1
    finally:
        gs.close()
    rec_ = g_hit / g if g else 0.0
    prec = e_hit / e if e else 0.0
    f1 = 2 * rec_ * prec / (rec_ + prec) if (rec_ + prec) else 0.0
    print(f"== HOLDS_POSITION tuple-F1 (position arm, db={args.db}) — $0, gold=M2.7 (unvalidated) ==")
    print(f"  cases scored: {n_cases}  gold positions: {g}  extracted: {e}")
    print(f"  recall={rec_:.0%}  precision={prec:.0%}  F1={f1:.2f}")
    print(f"  (compare: a1 'decision' arm E4 was UNCOVERED — no governed relation to score at all)")


if __name__ == "__main__":
    main()
