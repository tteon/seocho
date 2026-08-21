"""Ablation L1, task axis — does governance cost answer quality? OS vs BARE.

The remaining Level-1 axis (seocho-41a): Level-1 (ADR-0167) showed the OS
dominates the governance axes (0 leaks, full disclosure, bounded concurrency).
This asks the companion question — does routing an agent through the governed
path DEGRADE its ability to answer? If OS ≈ BARE on correctness, the governance
guarantees are *free* on the task axis, which is the honest form of the OS claim.

Both arms use the openai-agents SDK with a MARA-served model (the SDK is the
substrate; SEOCHO fills its guardrail/session/hooks sockets). BARE = a raw
`run_cypher` tool, no ontology guardrail, high row cap. OS = the governed session
agent (`Session.agent()`): ontology guardrail on the tool, workspace pinned,
row-cap + truncation disclosure. Same live FinBench graph, same questions,
deterministic numeric gold (no LLM judge — the gold is exact).

Usage:
  MARA_API_KEY=... python scripts/agentos/ablation_l1_task_parity.py \
      --container graphrag-neo4j --database finbenchl1 --model gpt-oss-120b \
      --out outputs/agentos/ablation_l1_task_parity.json
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

# Deterministic questions; gold is computed from the live graph at setup so the
# check needs no LLM judge. Each expects a single number in the final answer.
_QUESTIONS = [
    {"id": "company_count", "q": "How many Company nodes are in the graph?",
     "gold_cypher": "MATCH (c:Company) RETURN count(c) AS v"},
    {"id": "transfer_count", "q": "How many TRANSFER relationships are in the graph?",
     "gold_cypher": "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS v"},
    {"id": "flagged_accounts", "q": "How many Account nodes have flagged = true?",
     "gold_cypher": "MATCH (n:Account) WHERE n.flagged = true RETURN count(n) AS v"},
    {"id": "person_count", "q": "How many Person nodes are in the graph?",
     "gold_cypher": "MATCH (p:Person) RETURN count(p) AS v"},
    {"id": "accounts_owner_1019",
     "q": "How many Account nodes have owner_id equal to 1019?",
     "gold_cypher": "MATCH (n:Account) WHERE n.owner_id = 1019 RETURN count(n) AS v"},
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
    gold = {}
    try:
        with drv.session(database=database, default_access_mode="READ") as s:
            for qd in _QUESTIONS:
                gold[qd["id"]] = int(s.run(qd["gold_cypher"]).single()["v"])
    finally:
        drv.close()
    return gold


def _answer_has(number: int, text: str) -> bool:
    # accept the exact integer as a token, with or without thousands separators
    forms = {str(number), f"{number:,}"}
    toks = set(re.findall(r"[0-9][0-9,]*", text))
    return bool(forms & toks)


async def _ask(agent, question: str, max_turns: int) -> Dict[str, Any]:
    from agents import Runner, set_tracing_disabled

    # We run a MARA-served model, not OpenAI; the SDK's default trace upload
    # would hit OpenAI's platform with the wrong key. Disable it.
    set_tracing_disabled(True)
    try:
        result = await Runner.run(agent, question, max_turns=max_turns)
        text = str(result.final_output or "")
        usage = 0
        for r in getattr(result, "raw_responses", []) or []:
            u = getattr(r, "usage", None)
            usage += int(getattr(u, "total_tokens", 0) or 0)
        return {"text": text, "tokens": usage, "error": None}
    except Exception as exc:
        return {"text": "", "tokens": 0, "error": f"{type(exc).__name__}: {exc}"}


async def run(container, uri, database, model, ontology_path, max_turns) -> Dict[str, Any]:
    from seocho import Seocho
    from seocho.integrations.openai_agents import (make_graph_tool)
    from seocho.integrations.openai_agents import _mara_model
    from seocho.ontology import Ontology
    from seocho.store.graph import Neo4jGraphStore

    u, p = auth_of(container)
    gold = compute_gold(uri, u, p, database)
    onto = Ontology.from_yaml(ontology_path)
    store = Neo4jGraphStore(uri, u, p)

    # BARE arm: a raw run_cypher tool, no ontology guardrail, generous cap.
    from agents import Agent, ModelSettings
    bare_tool = make_graph_tool(store, database=database, row_cap=1000)
    bare_agent = Agent(
        name="bare_analyst",
        instructions=("You are an analyst. Use the run_cypher tool to query a "
                      "graph database and answer with the exact number."),
        model=_mara_model(model),
        model_settings=ModelSettings(temperature=0.0),
        tools=[bare_tool])

    # OS arm: the governed session agent — ontology guardrail + row cap +
    # truncation disclosure (the full SEOCHO path), same MARA model.
    client = Seocho(ontology=onto, graph_store=store, llm=object(),
                    workspace_id="default", agent_row_cap=1000)
    # seocho-933: the operating layer targets client.default_database, which the
    # ontology resolver sets to "finbenchlpg" (absent) rather than the graph we
    # loaded. Pin it to the actual database so the OS arm queries the same graph
    # as BARE — otherwise every OS query is a DatabaseNotFound and the
    # comparison measures the bug, not governance.
    client.default_database = database
    sess = client.session("analyst")
    os_agent = sess.agent(name="os_analyst", model=_mara_model(model))

    results = []
    for qd in _QUESTIONS:
        g = gold[qd["id"]]
        bare = await _ask(bare_agent, qd["q"], max_turns)
        os_r = await _ask(os_agent, qd["q"], max_turns)
        results.append({
            "id": qd["id"], "gold": g,
            "bare_correct": _answer_has(g, bare["text"]), "bare_tokens": bare["tokens"],
            "bare_error": bare["error"],
            "os_correct": _answer_has(g, os_r["text"]), "os_tokens": os_r["tokens"],
            "os_error": os_r["error"],
        })

    store.close() if hasattr(store, "close") else None
    n = len(results)
    return {
        "model": model, "database": database, "questions": n, "gold": gold,
        "bare_correct": sum(r["bare_correct"] for r in results),
        "os_correct": sum(r["os_correct"] for r in results),
        "bare_tokens_total": sum(r["bare_tokens"] for r in results),
        "os_tokens_total": sum(r["os_tokens"] for r in results),
        "cases": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="finbenchl1")
    ap.add_argument("--model", default="gpt-oss-120b")
    ap.add_argument("--ontology",
                    default="examples/finbench/finbench.ontology.yaml")
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rep = asyncio.run(run(args.container, args.uri, args.database, args.model,
                          args.ontology, args.max_turns))
    print(f"=== L1 task parity: BARE vs OS on {rep['database']} ({rep['model']}) ===")
    print(f"  {'question':22s} {'gold':>8s} {'BARE':>6s} {'OS':>6s}")
    for r in rep["cases"]:
        print(f"  {r['id']:22s} {r['gold']:>8d} "
              f"{('ok' if r['bare_correct'] else 'X'):>6s} "
              f"{('ok' if r['os_correct'] else 'X'):>6s}"
              + (f"   bare_err={r['bare_error']}" if r['bare_error'] else "")
              + (f"   os_err={r['os_error']}" if r['os_error'] else ""))
    print(f"\n  correct: BARE {rep['bare_correct']}/{rep['questions']}  "
          f"OS {rep['os_correct']}/{rep['questions']}")
    print(f"  tokens:  BARE {rep['bare_tokens_total']}  OS {rep['os_tokens_total']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
