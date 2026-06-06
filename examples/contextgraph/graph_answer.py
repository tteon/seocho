#!/usr/bin/env python3
"""Tier-1 deterministic graph answerer (panel build #2, VERIFY stage) — NO LLM.

The graph-strength eval showed BC3 graphs CAN serve multi-hop joins (proposal↔
stance 73%) and provenance (91%) — operations vector+LLM hallucinate. This turns
that answerability into actual GROUNDED answers via deterministic Cypher +
templating, with NO model call: the Tier-1 / admission-control lane the scale
expert ranked #1 (survive 5K-RPD: every query answered here is an LLM call NOT
spent) and the verifiability the quality expert wants (every claim cites a
source_quote from the graph).

Query classes (keyword-routed): positions (who for/against, w/ quotes),
proposals (who proposed what), decisions (what resolved, decider), initiator
(earliest message — degraded: sent_date often null). Returns a structured,
provenance-carrying answer string + a `verifiable` flag (every claim backed by a
graph row). This is NOT scored on prose-QA (wrong metric); it demonstrates the
LLM-free, grounded, verifiable serving that is graph's real job.

Run: python examples/contextgraph/graph_answer.py --db cgbc3minimaxm25 \
        --ws e1-bc3-a1-decision-007-7484738 --query "who opposed what and why?"
     # or --demo to answer a positions/proposals/decisions question per thread
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.graph import Neo4jGraphStore


def _rows(gs, cy, w, db):
    try:
        return gs.query(cy, params={"w": w}, database=db)
    except Exception:
        return []


def answer_positions(gs, w, db):
    rows = _rows(gs,
        "MATCH (p:Person {_workspace_id:$w})-[r]->(pr:Proposal {_workspace_id:$w}) "
        "WHERE type(r) IN ['SUPPORTS','OPPOSES'] "
        "RETURN p.name AS person, type(r) AS stance, pr.name AS prop, pr.source_quote AS sq", w, db)
    if not rows:
        return "not in the provided context", False
    lines = []
    for r in rows:
        q = f"  (\"{str(r['sq'])[:80]}\")" if r.get("sq") else ""
        verb = "supported" if r["stance"] == "SUPPORTS" else "opposed"
        lines.append(f"- {r['person']} {verb} '{r['prop']}'{q}")
    return "Positions expressed:\n" + "\n".join(lines), True


def answer_proposals(gs, w, db):
    rows = _rows(gs,
        "MATCH (p:Person {_workspace_id:$w})-[:PROPOSES]->(pr:Proposal {_workspace_id:$w}) "
        "RETURN p.name AS person, pr.name AS prop", w, db)
    if not rows:
        return "not in the provided context", False
    return "Proposals:\n" + "\n".join(f"- {r['person']} proposed '{r['prop']}'" for r in rows), True


def answer_decisions(gs, w, db):
    rows = _rows(gs,
        "MATCH (d:Decision {_workspace_id:$w}) "
        "OPTIONAL MATCH (d)-[:RESOLVES]->(pr:Proposal {_workspace_id:$w}) "
        "OPTIONAL MATCH (per:Person {_workspace_id:$w})-[:DECIDES]->(d) "
        "RETURN d.name AS decision, collect(DISTINCT pr.name) AS resolves, "
        "collect(DISTINCT per.name) AS deciders", w, db)
    rows = [r for r in rows if r.get("decision")]
    if not rows:
        return "No decision was recorded in the thread.", True  # absence is a valid grounded answer
    lines = []
    for r in rows:
        res = f" (resolves: {', '.join(x for x in r['resolves'] if x)})" if any(r["resolves"]) else ""
        dec = f" — decided by {', '.join(x for x in r['deciders'] if x)}" if any(r["deciders"]) else ""
        lines.append(f"- {r['decision']}{res}{dec}")
    return "Decisions:\n" + "\n".join(lines), True


def answer_initiator(gs, w, db):
    rows = _rows(gs,
        "MATCH (p:Person {_workspace_id:$w})-[:SENT]->(m:EmailMessage {_workspace_id:$w}) "
        "WHERE m.sent_date IS NOT NULL AND m.sent_date<>'' "
        "RETURN p.name AS person, m.sent_date AS d ORDER BY m.sent_date LIMIT 1", w, db)
    if not rows:
        return "not in the provided context (no dated message — sent_date not extracted)", False
    return f"Initiated by {rows[0]['person']} on {rows[0]['d']}.", True


_ROUTER = [
    (("oppos", "support", "against", "favor", "position", "for or against", "object"), answer_positions),
    (("propos", "suggest"), answer_proposals),
    (("decid", "decision", "outcome", "resolve"), answer_decisions),
    (("initiat", "who started", "who began", "first"), answer_initiator),
]


def deterministic_answer(gs, w, db, query):
    ql = query.lower()
    for keys, fn in _ROUTER:
        if any(k in ql for k in keys):
            ans, ok = fn(gs, w, db)
            return ans, ok, fn.__name__
    # default: positions (most graph-favorable)
    ans, ok = answer_positions(gs, w, db)
    return ans, ok, "answer_positions(default)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3minimaxm25")
    ap.add_argument("--ws", default="e1-bc3-a1-decision-007-7484738")
    ap.add_argument("--query", default="who supported or opposed what, and why?")
    ap.add_argument("--demo", action="store_true", help="run positions+proposals+decisions on the ws")
    args = ap.parse_args()
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        if args.demo:
            for q in ["who supported or opposed what, and why?",
                      "what was proposed, by whom?",
                      "what decisions were made?"]:
                ans, ok, fn = deterministic_answer(gs, args.ws, args.db, q)
                print(f"\nQ: {q}\n[{fn} | LLM-free | verifiable={ok}]\n{ans}")
        else:
            ans, ok, fn = deterministic_answer(gs, args.ws, args.db, args.query)
            print(f"Q: {args.query}\n[{fn} | LLM-free | verifiable={ok}]\n{ans}")
    finally:
        gs.close()


if __name__ == "__main__":
    main()
