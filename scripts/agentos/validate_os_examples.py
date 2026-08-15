"""(c) end-to-end validation — does shipping worked examples in the REAL
os.build_agent cut the guardrail repair loop?

ADR-0169's killer used hand-written examples in its own harness. This runs the
SHIPPED governed agent (`Session.agent()` → `SeochoOS.build_agent`, which now
appends ontology-derived worked examples, #524) against a BARE agent, and reads
the guardrail's own ledger (allowed/rejected/by_reason) after each run — so
"did examples reduce rejections?" is measured on the production path, not a
proxy. A rejection = a repair-loop turn; near-zero rejections = first-try
conformance.

Usage:
  MARA_API_KEY=... python scripts/agentos/validate_os_examples.py \
      --database finbenchl1 --models gpt-oss-120b,gemma-4-31B-it \
      --out outputs/agentos/validate_os_examples.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

_Q = [
    ("company_count", "How many Company nodes are in the graph?",
     "MATCH (c:Company) RETURN count(c) AS v"),
    ("transfer_count", "How many TRANSFER relationships are in the graph?",
     "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS v"),
    ("flagged", "How many Account nodes have flagged = true?",
     "MATCH (n:Account) WHERE n.flagged = true RETURN count(n) AS v"),
    ("risk_tier_1", "How many Account nodes have risk_tier equal to 1?",
     "MATCH (n:Account) WHERE n.risk_tier = 1 RETURN count(n) AS v"),
    ("persons_owning", "How many distinct Person nodes own at least one Account?",
     "MATCH (p:Person)-[:OWN]->(:Account) RETURN count(DISTINCT p) AS v"),
    ("transfer_sources",
     "How many distinct Account nodes are the source of a TRANSFER?",
     "MATCH (a:Account)-[:TRANSFER]->() RETURN count(DISTINCT a) AS v"),
]


def auth_of(container):
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit("no auth")


def gold(uri, u, p, db):
    from neo4j import GraphDatabase
    d = GraphDatabase.driver(uri, auth=(u, p))
    try:
        with d.session(database=db, default_access_mode="READ") as s:
            return {qid: int(s.run(cy).single()["v"]) for qid, _, cy in _Q}
    finally:
        d.close()


def _has(n, text):
    return bool({str(n), f"{n:,}"} & set(re.findall(r"[0-9][0-9,]*", text)))


async def _ask(agent, q, max_turns):
    from agents import Runner, set_tracing_disabled
    set_tracing_disabled(True)
    try:
        r = await Runner.run(agent, q, max_turns=max_turns)
        toks = sum(int(getattr(getattr(x, "usage", None), "total_tokens", 0) or 0)
                   for x in getattr(r, "raw_responses", []) or [])
        return str(r.final_output or ""), toks, None
    except Exception as exc:
        return "", 0, f"{type(exc).__name__}: {exc}"


def _ledger_of(agent):
    try:
        return agent.tools[0].tool_input_guardrails[0].ledger
    except Exception:
        return None


async def run(container, uri, database, models, ontology_path, max_turns):
    from agents import Agent, ModelSettings

    from seocho import Seocho
    from seocho.integrations.openai_agents import _mara_model, make_graph_tool
    from seocho.ontology import Ontology
    from seocho.store.graph import Neo4jGraphStore

    u, p = auth_of(container)
    g = gold(uri, u, p, database)
    onto = Ontology.from_yaml(ontology_path)
    store = Neo4jGraphStore(uri, u, p)

    report = {"database": database, "gold": g, "by_model": {}}
    for model in models:
        os_cases, bare_cases = [], []
        for qid, q, _ in _Q:
            # OS arm: the SHIPPED governed agent (with worked examples, #524)
            client = Seocho(ontology=onto, graph_store=store, llm=object(),
                            workspace_id="default", agent_row_cap=1000)
            client.default_database = database          # seocho-933 workaround
            os_agent = client.session("a").agent(name="os", model=_mara_model(model))
            text, toks, err = await _ask(os_agent, q, max_turns)
            led = _ledger_of(os_agent)
            summ = led.summary() if led else {}
            os_cases.append({"id": qid, "correct": _has(g[qid], text),
                             "tokens": toks, "err": err,
                             "guardrail_rejected": summ.get("rejected", None),
                             "guardrail_allowed": summ.get("allowed", None),
                             "reasons": summ.get("by_reason", {})})
            # BARE arm: raw tool, no schema, no guardrail
            bare = Agent(name="bare",
                         instructions="You are an analyst. Use run_cypher to "
                         "query a graph and answer with the exact number.",
                         model=_mara_model(model),
                         model_settings=ModelSettings(temperature=0.0),
                         tools=[make_graph_tool(store, database=database, row_cap=1000)])
            btext, btoks, berr = await _ask(bare, q, max_turns)
            bare_cases.append({"id": qid, "correct": _has(g[qid], btext),
                               "tokens": btoks, "err": berr})
        report["by_model"][model] = {
            "os_correct": sum(c["correct"] for c in os_cases),
            "bare_correct": sum(c["correct"] for c in bare_cases),
            "n": len(_Q),
            "os_guardrail_rejections": sum((c["guardrail_rejected"] or 0) for c in os_cases),
            "os_tokens": sum(c["tokens"] for c in os_cases),
            "bare_tokens": sum(c["tokens"] for c in bare_cases),
            "os_cases": os_cases,
        }
    store.close() if hasattr(store, "close") else None
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="finbenchl1")
    ap.add_argument("--models", default="gpt-oss-120b,gemma-4-31B-it")
    ap.add_argument("--ontology", default="examples/finbench/finbench.ontology.yaml")
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rep = asyncio.run(run(args.container, args.uri, args.database,
                          [m.strip() for m in args.models.split(",") if m.strip()],
                          args.ontology, args.max_turns))
    for model, r in rep["by_model"].items():
        print(f"\n=== {model} ({r['n']} questions) — shipped OS agent (with examples) ===")
        print(f"  correct: OS {r['os_correct']}/{r['n']}  BARE {r['bare_correct']}/{r['n']}")
        print(f"  OS guardrail rejections (repair-loop turns): {r['os_guardrail_rejections']}")
        print(f"  tokens: OS {r['os_tokens']}  BARE {r['bare_tokens']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
