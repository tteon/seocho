#!/usr/bin/env python3
"""approach2 — SHACL+SKOS graph + ReAct reasoning (vs approach1's one-shot dump).

Round-2 showed approach1 filled the graph STRUCTURE (CQ 30→60%, stance 0→73%) but
answer quality stayed flat → the binding constraint is how the graph is USED, not
what's missing. approach2 tests the reasoning lever: instead of serializing the
whole subgraph once, a ReAct loop lets the model QUERY the (same approach1) graph
iteratively (Thought → Action → Observation) and synthesize from what it pulls.

Reuses the approach1 graphs (e1-bc3-a1-decision-<tid> in cgbc3minimaxm25). Tools
are a fixed set of parameterized, read-only, workspace-scoped Cypher reads (§8).
Writes finder-partial JSON (retrieval="graph", arm="react") so finder_judge can
score it head-to-head with approach1 graph@decision and baseline, same judge.

Resilient: per-(thread,case) try/except + resume (skip a case whose partial
exists) so a flaky MARA never loses progress or crashes the run.

Run: python examples/contextgraph/run_react.py --threads 15 --model MiniMax-M2.7
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from seocho.store.graph import Neo4jGraphStore
from seocho.store.llm import create_llm_backend

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
SEP = "===EVIDENCE_BOUNDARY==="
DB = "cgbc3minimaxm25"
WS_RUN = "e1-bc3-a1"   # reuse approach1 (SHACL+SKOS) graphs
MAX_STEPS = 4
_STANCE = ["SUPPORTS", "OPPOSES", "HAS_STANCE", "AGAINST", "FAVORS", "CONTRADICTS"]


# ---- graph-read tools (parameterized, read-only, workspace-scoped) ----
def t_list_proposals(gs, w):
    rows = gs.query("MATCH (p:Proposal {_workspace_id:$w}) "
                    "OPTIONAL MATCH (per:Person {_workspace_id:$w})-[:PROPOSES]->(p) "
                    "RETURN p.name AS proposal, collect(DISTINCT per.name) AS proposers LIMIT 40",
                    params={"w": w}, database=DB)
    return [{"proposal": r["proposal"], "proposers": [x for x in r["proposers"] if x]} for r in rows]


def t_stances_on(gs, w, arg):
    q = str(arg or "")
    rows = gs.query(f"MATCH (per:Person {{_workspace_id:$w}})-[r]->(p:Proposal {{_workspace_id:$w}}) "
                    f"WHERE type(r) IN {_STANCE} AND ($q='' OR toLower(p.name) CONTAINS toLower($q)) "
                    f"RETURN per.name AS person, type(r) AS stance, p.name AS proposal LIMIT 40",
                    params={"w": w, "q": q}, database=DB)
    return [{"person": r["person"], "stance": r["stance"], "proposal": r["proposal"]} for r in rows]


def t_decisions(gs, w):
    rows = gs.query("MATCH (d:Decision {_workspace_id:$w}) "
                    "OPTIONAL MATCH (d)-[:RESOLVES]->(p:Proposal {_workspace_id:$w}) "
                    "OPTIONAL MATCH (per:Person {_workspace_id:$w})-[:DECIDES]->(d) "
                    "RETURN d.name AS decision, collect(DISTINCT p.name) AS resolves, "
                    "collect(DISTINCT per.name) AS deciders LIMIT 20",
                    params={"w": w}, database=DB)
    return [{"decision": r["decision"], "resolves": [x for x in r["resolves"] if x],
             "deciders": [x for x in r["deciders"] if x]} for r in rows]


def t_messages(gs, w):
    rows = gs.query("MATCH (per:Person {_workspace_id:$w})-[:SENT]->(m:EmailMessage {_workspace_id:$w}) "
                    "RETURN per.name AS sender, m.sent_date AS sent_date, m.subject AS subject "
                    "ORDER BY m.sent_date LIMIT 40", params={"w": w}, database=DB)
    return [{"sender": r["sender"], "sent_date": r["sent_date"], "subject": r["subject"]} for r in rows]


TOOLS = {
    "list_proposals": "list_proposals — all proposals and who proposed each",
    "stances_on": "stances_on(proposal) — who SUPPORTS/OPPOSES a proposal (arg=proposal gist, '' = all)",
    "decisions": "decisions — decisions made, what proposal each resolves, and the decider",
    "messages": "messages — messages with sender and sent_date, time-ordered (who initiated/when)",
    "finish": "finish(answer) — give the final answer",
}

_SYS = (
    "You answer a question about an email decision thread by QUERYING a knowledge graph "
    "with the available actions, then synthesizing. Reason step by step.\n"
    "Available actions:\n" + "\n".join(f"  - {d}" for d in TOOLS.values()) + "\n\n"
    "Each step output STRICT JSON only: {\"thought\":\"…\",\"action\":\"<name>\",\"arg\":\"…\"}. "
    "Use the observations to decide the next action. When you have enough, use action \"finish\" "
    "with the full answer in \"arg\" (name participants, proposals, positions for/against, and the "
    "outcome; say 'not in the provided context' if the graph lacks it — do NOT invent)."
)


def _parse_action(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?|\n?```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return {"action": "finish", "arg": t[:600]}
    try:
        d = json.loads(m.group(0))
        return {"action": str(d.get("action", "finish")), "arg": d.get("arg", "")}
    except Exception:
        return {"action": "finish", "arg": t[:600]}


def react_answer(llm, gs, w, question):
    """Run the ReAct loop; return (answer, n_steps, trace)."""
    transcript = f"Question: {question}\n"
    for step in range(MAX_STEPS):
        try:
            r = llm.complete(system=_SYS, user=transcript + "\nNext action (JSON):")
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {str(e)[:80]}", step, transcript
        act = _parse_action(getattr(r, "text", "") or getattr(r, "content", "") or "")
        a, arg = act["action"], act["arg"]
        if a == "finish":
            return str(arg) or "not in the provided context", step + 1, transcript
        try:
            if a == "list_proposals":
                obs = t_list_proposals(gs, w)
            elif a == "stances_on":
                obs = t_stances_on(gs, w, arg)
            elif a == "decisions":
                obs = t_decisions(gs, w)
            elif a == "messages":
                obs = t_messages(gs, w)
            else:
                obs = {"error": f"unknown action {a}"}
        except Exception as e:
            obs = {"error": f"{type(e).__name__}"}
        transcript += f"\nAction: {a}({arg})\nObservation: {json.dumps(obs, default=str)[:1200]}\n"
    # out of steps → ask for final synthesis
    try:
        r = llm.complete(system=_SYS, user=transcript + "\nYou are out of steps. Give the final answer now (action finish).")
        return str(_parse_action(getattr(r, "text", "") or "")["arg"]) or "not in the provided context", MAX_STEPS, transcript
    except Exception as e:
        return f"ERROR: {type(e).__name__}", MAX_STEPS, transcript


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="MiniMax-M2.7")
    ap.add_argument("--provider", default="mara")
    ap.add_argument("--threads", type=int, default=15)
    ap.add_argument("--run", default="e1-bc3-a2-react")
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
    llm = create_llm_backend(provider=args.provider, model=args.model)
    print(f"== approach2 ReAct: model={args.model} db={DB} graphs={WS_RUN} threads={len(tids)} ==\n")

    n_done = n_err = 0
    for tid in tids:
        w = f"{WS_RUN}-decision-{tid}"
        for c in by_thread[tid]:
            slice_, q, gold = c["slice"], c["query"], c["answer"]
            outp = out_dir / f"{slice_}_{c['_id']}_graph_react.json".replace("#", "_")
            if outp.exists():  # resume
                continue
            ans, steps, _ = react_answer(llm, gs, w, q)
            rec = {"_id": f"{c['_id']}|graph|react", "slice": slice_, "category": "Decision",
                   "query": q, "expected_answer": gold, "answer": ans,
                   "retrieval": "graph", "mode": "graph", "arm": "react",
                   "model": f"{args.provider}/{args.model}", "react_steps": steps,
                   "evaluation": {"number_overlap_ratio": 0.0}}
            outp.write_text(json.dumps(rec, default=str))
            if ans.startswith("ERROR:"):
                n_err += 1
            else:
                n_done += 1
            print(f"  [{c['_id']} {slice_}] steps={steps} ans={ans[:70]!r}")
    gs.close()
    print(f"\nwrote partials -> {out_dir}  (ok={n_done} err={n_err})")
    print("judge: python scripts/benchmarks/finder_judge.py --judge-domain decision "
          f"--judge-llms mara/gpt-oss-120b --inputs 'outputs/evaluation/contextgraph/{args.run}/partial/*.json' "
          f"--out outputs/evaluation/contextgraph/{args.run}_judged.json")


if __name__ == "__main__":
    main()
