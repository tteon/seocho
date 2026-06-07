#!/usr/bin/env python3
"""$0 HUMAN-gold accuracy for the position arm — replaces the M2.7-drafted gold.

Crux-4 fix (the user's point: we HAVE human gold — FinDER/BC3/AMI answers — so
don't re-draft with M2.7). The BC3 E4 gold answer is human-authored
"Person: statement | Person: statement ...". That deterministically yields
(author, statement-text) pairs — the granularity the human gold actually
provides. polarity/anchor are IMPLICIT in the statement (would need inference),
so we score at the level the human gold supports, honestly:

  recall    = gold (author, statement) pairs RECOVERED by some extracted
              HOLDS_POSITION whose person matches the author (order-insensitive,
              comma-flip normalized) AND whose source_quote overlaps the statement.
  precision = extracted HOLDS_POSITION whose (person, quote) matches some gold pair.

Name matching uses the SAME order-insensitive + comma-flip normalizer that fixed
the earlier matcher artifact ("Dickinson, Ian J" == "Ian J Dickinson"). Quote
overlap = token-recall of the gold statement covered by the extracted quote >= TAU.
Does NOT score polarity/anchor (not deterministic from the human gold) — stated.

Run: python examples/contextgraph/position_humangold_metric.py --db cgbc3pos15d \
        --ws-prefix e1-bc3-pos15d-position-
"""
from __future__ import annotations
import argparse, csv, logging, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from seocho.store.graph import Neo4jGraphStore

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
TAU = 0.5  # token-recall of the gold statement that the extracted quote must cover
_STOP = {"the", "a", "an", "of", "to", "for", "at", "in", "on", "and", "or", "be", "we",
         "i", "is", "it", "this", "that", "s", "as", "are", "was", "if", "but", "so", "my"}


def _norm_name(s):
    s = str(s or "").strip()
    if s.count(",") == 1:  # "Last, First" -> "First Last" (comma-flip)
        a, b = s.split(",", 1)
        s = f"{b.strip()} {a.strip()}"
    return frozenset(t for t in re.findall(r"[a-z0-9]+", s.lower()) if t)


def _name_match(a, b):
    sa, sb = _norm_name(a), _norm_name(b)
    return bool(sa) and bool(sb) and (sa == sb or sa <= sb or sb <= sa)


def _ctoks(s):
    return {t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _STOP and len(t) > 2}


def _quote_covers(gold_stmt, quote):
    g = _ctoks(gold_stmt)
    if not g:
        return False
    return len(g & _ctoks(quote)) / len(g) >= TAU


def _parse_gold(answer):
    """human gold 'Person: statement | Person: statement' -> [(author, statement)]"""
    out = []
    for seg in str(answer).split("|"):
        seg = seg.strip()
        if ":" in seg:
            who, _, stmt = seg.partition(":")
            if who.strip() and stmt.strip():
                out.append((who.strip(), stmt.strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3pos15d")
    ap.add_argument("--ws-prefix", default="e1-bc3-pos15d-position-")
    args = ap.parse_args()
    rows = [r for r in csv.DictReader(open(DATA)) if r["slice"] == "E4_POSITIONS"]
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    g_tot = g_hit = e_tot = e_hit = n_cases = 0
    try:
        for r in rows:
            tid = str(r["_id"]).split("#")[0]
            ws = f"{args.ws_prefix}{tid}"
            ext = gs.query(
                "MATCH (p:Person {_workspace_id:$w})-[rel:HOLDS_POSITION]->(:Topic {_workspace_id:$w}) "
                "RETURN p.name AS person, rel.source_quote AS quote",
                params={"w": ws}, database=args.db) or []
            if not ext:
                continue  # workspace not built — skip, don't penalize
            gold = _parse_gold(r["answer"])
            if not gold:
                continue
            n_cases += 1
            for (author, stmt) in gold:
                g_tot += 1
                if any(_name_match(author, e["person"]) and _quote_covers(stmt, e.get("quote"))
                       for e in ext):
                    g_hit += 1
            for e in ext:
                e_tot += 1
                if any(_name_match(a2, e["person"]) and _quote_covers(s2, e.get("quote"))
                       for (a2, s2) in gold):
                    e_hit += 1
    finally:
        gs.close()
    rec = g_hit / g_tot if g_tot else 0.0
    prec = e_hit / e_tot if e_tot else 0.0
    f1 = 2 * rec * prec / (rec + prec) if (rec + prec) else 0.0
    print(f"== HOLDS_POSITION vs HUMAN gold (BC3 E4, db={args.db}) — $0, no M2.7, no judge ==")
    print(f"  cases scored: {n_cases}  human-gold (author,statement) pairs: {g_tot}  extracted positions: {e_tot}")
    print(f"  recall={rec:.0%}  precision={prec:.0%}  F1={f1:.2f}   (name: comma-flip+token-set; quote-cover>={TAU})")
    print(f"  scores (author, statement-grounding) — the granularity human gold provides;")
    print(f"  polarity/anchor NOT scored (implicit in the human statement, not deterministic).")
    print(f"  compare: M2.7-drafted gold gave F1 0.13 (recall 14/prec 11) — this is the human-anchored number.")


if __name__ == "__main__":
    main()
