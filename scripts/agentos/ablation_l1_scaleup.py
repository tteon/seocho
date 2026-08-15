"""Ablation L1 task axis, scale-up — why does the OS spend more tokens, and
does the spend buy better answers? Across models, question difficulty, and a
token-cost breakdown.

Follows ADR-0168 (n=1, one model, 5 easy counts) with: (1) a harder question mix
(easy counts, on-schema property filters, and relational-distinct questions where
knowing the relationship name should HELP a schema-carrying agent), (2) two
models (a strong one and a second family, per PORT-1), and (3) a token
breakdown that separates the two cost causes — first-turn tokens (the
schema-in-context overhead, a feature) vs total (adds guardrail-retry friction).

BARE = raw run_cypher tool, one-line instruction, no schema, no guardrail.
OS = governed session agent (schema-in-context + ontology guardrail + row cap).
Same live FinBench graph, deterministic numeric gold (no LLM judge).

Usage:
  MARA_API_KEY=... python scripts/agentos/ablation_l1_scaleup.py \
      --database finbenchl1 --models gpt-oss-120b,DeepSeek-V3.1 \
      --out outputs/agentos/ablation_l1_scaleup.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# category: easy | filter | relational (verified gold on finbenchl1)
_QUESTIONS = [
    {"id": "company_count", "cat": "easy",
     "q": "How many Company nodes are in the graph?",
     "gold_cypher": "MATCH (c:Company) RETURN count(c) AS v"},
    {"id": "person_count", "cat": "easy",
     "q": "How many Person nodes are in the graph?",
     "gold_cypher": "MATCH (p:Person) RETURN count(p) AS v"},
    {"id": "transfer_count", "cat": "easy",
     "q": "How many TRANSFER relationships are in the graph?",
     "gold_cypher": "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS v"},
    {"id": "own_count", "cat": "easy",
     "q": "How many OWN relationships are in the graph?",
     "gold_cypher": "MATCH ()-[o:OWN]->() RETURN count(o) AS v"},
    {"id": "flagged_accounts", "cat": "filter",
     "q": "How many Account nodes have flagged = true?",
     "gold_cypher": "MATCH (n:Account) WHERE n.flagged = true RETURN count(n) AS v"},
    {"id": "risk_tier_1", "cat": "filter",
     "q": "How many Account nodes have risk_tier equal to 1?",
     "gold_cypher": "MATCH (n:Account) WHERE n.risk_tier = 1 RETURN count(n) AS v"},
    {"id": "persons_owning", "cat": "relational",
     "q": "How many distinct Person nodes own at least one Account?",
     "gold_cypher": "MATCH (p:Person)-[:OWN]->(:Account) RETURN count(DISTINCT p) AS v"},
    {"id": "transfer_sources", "cat": "relational",
     "q": "How many distinct Account nodes are the source of at least one "
          "TRANSFER relationship?",
     "gold_cypher": "MATCH (a:Account)-[:TRANSFER]->() RETURN count(DISTINCT a) AS v"},
]


def auth_of(container: str):
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit(f"no NEO4J_AUTH on {container}")


def compute_gold(uri, u, p, database) -> Dict[str, int]:
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=(u, p))
    try:
        with drv.session(database=database, default_access_mode="READ") as s:
            return {qd["id"]: int(s.run(qd["gold_cypher"]).single()["v"])
                    for qd in _QUESTIONS}
    finally:
        drv.close()


def _answer_has(number: int, text: str) -> bool:
    forms = {str(number), f"{number:,}"}
    toks = set(re.findall(r"[0-9][0-9,]*", text))
    return bool(forms & toks)


async def _ask(agent, question: str, max_turns: int) -> Dict[str, Any]:
    from agents import Runner, set_tracing_disabled
    set_tracing_disabled(True)
    try:
        result = await Runner.run(agent, question, max_turns=max_turns)
        text = str(result.final_output or "")
        raw = getattr(result, "raw_responses", []) or []
        per = [int(getattr(getattr(r, "usage", None), "total_tokens", 0) or 0)
               for r in raw]
        return {"text": text, "total_tokens": sum(per),
                "first_turn_tokens": per[0] if per else 0,
                "turns": len(per), "error": None}
    except Exception as exc:
        return {"text": "", "total_tokens": 0, "first_turn_tokens": 0,
                "turns": 0, "error": f"{type(exc).__name__}: {exc}"}


def _build_agents(onto, store, database, model):
    from agents import Agent, ModelSettings

    from seocho import Seocho
    from seocho.integrations.openai_agents import _mara_model, make_graph_tool

    bare = Agent(
        name="bare_analyst",
        instructions=("You are an analyst. Use the run_cypher tool to query a "
                      "graph database and answer with the exact number."),
        model=_mara_model(model),
        model_settings=ModelSettings(temperature=0.0),
        tools=[make_graph_tool(store, database=database, row_cap=1000)])

    client = Seocho(ontology=onto, graph_store=store, llm=object(),
                    workspace_id="default", agent_row_cap=1000)
    client.default_database = database          # seocho-933 workaround
    sess = client.session("analyst")
    os_agent = sess.agent(name="os_analyst", model=_mara_model(model))
    return bare, os_agent


async def run(container, uri, database, models, ontology_path, max_turns):
    from seocho.ontology import Ontology
    from seocho.store.graph import Neo4jGraphStore

    u, p = auth_of(container)
    gold = compute_gold(uri, u, p, database)
    onto = Ontology.from_yaml(ontology_path)
    store = Neo4jGraphStore(uri, u, p)

    by_model = {}
    for model in models:
        bare_agent, os_agent = _build_agents(onto, store, database, model)
        cases = []
        for qd in _QUESTIONS:
            g = gold[qd["id"]]
            b = await _ask(bare_agent, qd["q"], max_turns)
            o = await _ask(os_agent, qd["q"], max_turns)
            cases.append({
                "id": qd["id"], "cat": qd["cat"], "gold": g,
                "bare_ok": _answer_has(g, b["text"]), "bare_tok": b["total_tokens"],
                "bare_turns": b["turns"], "bare_err": b["error"],
                "os_ok": _answer_has(g, o["text"]), "os_tok": o["total_tokens"],
                "os_first_tok": o["first_turn_tokens"], "os_turns": o["turns"],
                "os_err": o["error"],
            })
        n = len(cases)
        by_model[model] = {
            "cases": cases,
            "bare_correct": sum(c["bare_ok"] for c in cases),
            "os_correct": sum(c["os_ok"] for c in cases),
            "questions": n,
            "bare_tok": sum(c["bare_tok"] for c in cases),
            "os_tok": sum(c["os_tok"] for c in cases),
            "os_first_tok": sum(c["os_first_tok"] for c in cases),
            "os_retry_tok": sum(c["os_tok"] - c["os_first_tok"] for c in cases),
            "os_extra_turns": sum(max(0, c["os_turns"] - 1) for c in cases),
            "by_cat": {},
        }
        for cat in ("easy", "filter", "relational"):
            cc = [c for c in cases if c["cat"] == cat]
            by_model[model]["by_cat"][cat] = {
                "n": len(cc),
                "bare_ok": sum(c["bare_ok"] for c in cc),
                "os_ok": sum(c["os_ok"] for c in cc)}
    store.close() if hasattr(store, "close") else None
    return {"database": database, "models": models, "gold": gold,
            "by_model": by_model}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="finbenchl1")
    ap.add_argument("--models", default="gpt-oss-120b,DeepSeek-V3.1")
    ap.add_argument("--ontology",
                    default="examples/finbench/finbench.ontology.yaml")
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    rep = asyncio.run(run(args.container, args.uri, args.database, models,
                          args.ontology, args.max_turns))
    for model, r in rep["by_model"].items():
        print(f"\n=== {model} on {rep['database']} ({r['questions']} questions) ===")
        print(f"  {'question':20s} {'cat':10s} {'gold':>8s} {'BARE':>5s} {'OS':>4s}")
        for c in r["cases"]:
            print(f"  {c['id']:20s} {c['cat']:10s} {c['gold']:>8d} "
                  f"{('ok' if c['bare_ok'] else 'X'):>5s} "
                  f"{('ok' if c['os_ok'] else 'X'):>4s}")
        print(f"  correct: BARE {r['bare_correct']}/{r['questions']}  "
              f"OS {r['os_correct']}/{r['questions']}   by cat "
              + " ".join(f"{k}:B{v['bare_ok']}/O{v['os_ok']}"
                         for k, v in r["by_cat"].items()))
        print(f"  tokens:  BARE {r['bare_tok']}  OS {r['os_tok']} "
              f"(schema/first-turn {r['os_first_tok']} + retry {r['os_retry_tok']}; "
              f"{r['os_extra_turns']} extra turns)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
