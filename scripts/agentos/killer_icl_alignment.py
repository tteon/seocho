"""Killer experiment — in-context specification vs enforced alignment.

Two axes on the same live-graph agent task (schema-in-context = ICL of the
contract; guardrail = hard enforcement):

  COMPOSITION (what schema material is in the prompt — the ICL dose):
    none           : one-line instruction, no schema
    labels         : labels + relationship types only
    full           : SEOCHO's full schema block (labels+rels+props+params)
    full_examples  : full + two worked Cypher examples (the few-shot ICL move)

  ENFORCEMENT (the guardrail):
    soft : no enforcement — the tool runs whatever the model emits, but the
           ontology validator's verdict is RECORDED on every query (drift is
           measured, not blocked).
    hard : the validator rejects off-contract queries (the model must re-emit).

Configs run: {none,labels,full,full_examples}×soft  +  full×hard. So the soft
sweep is the ICL dose-response, and full-soft vs full-hard is the
"context proposes, contract disposes" gap — how often soft alignment drifts
off-contract, and whether hard enforcement recovers it.

Metrics per (model, config): correctness vs deterministic gold; conformance
rate (fraction of emitted queries that pass the ontology validator); drift
(soft queries that WOULD be rejected but ran anyway); tokens; turns.

Usage:
  MARA_API_KEY=... python scripts/agentos/killer_icl_alignment.py \
      --database finbenchl1 --models gpt-oss-120b,gemma-4-31B-it \
      --out outputs/agentos/killer_icl_alignment.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

_Q = [
    {"id": "company_count", "cat": "easy",
     "q": "How many Company nodes are in the graph?",
     "g": "MATCH (c:Company) RETURN count(c) AS v"},
    {"id": "transfer_count", "cat": "easy",
     "q": "How many TRANSFER relationships are in the graph?",
     "g": "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS v"},
    {"id": "flagged", "cat": "filter",
     "q": "How many Account nodes have flagged = true?",
     "g": "MATCH (n:Account) WHERE n.flagged = true RETURN count(n) AS v"},
    {"id": "risk_tier_1", "cat": "filter",
     "q": "How many Account nodes have risk_tier equal to 1?",
     "g": "MATCH (n:Account) WHERE n.risk_tier = 1 RETURN count(n) AS v"},
    {"id": "persons_owning", "cat": "relational",
     "q": "How many distinct Person nodes own at least one Account?",
     "g": "MATCH (p:Person)-[:OWN]->(:Account) RETURN count(DISTINCT p) AS v"},
    {"id": "transfer_sources", "cat": "relational",
     "q": "How many distinct Account nodes are the source of a TRANSFER?",
     "g": "MATCH (a:Account)-[:TRANSFER]->() RETURN count(DISTINCT a) AS v"},
]

_CONFIGS = [
    ("none", "soft"), ("labels", "soft"), ("full", "soft"),
    ("full_examples", "soft"), ("full", "hard"),
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


def compute_gold(uri, u, p, database):
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=(u, p))
    try:
        with drv.session(database=database, default_access_mode="READ") as s:
            return {q["id"]: int(s.run(q["g"]).single()["v"]) for q in _Q}
    finally:
        drv.close()


def _instruction(composition, onto):
    base = ("You are an analyst. Use the run_cypher tool to query a graph "
            "database and answer with the exact number.")
    if composition == "none":
        return base
    from seocho.query.hybrid_planner import policy_from_ontology, schema_for_prompt
    policy = policy_from_ontology(onto)
    if composition == "labels":
        labels = list(getattr(onto, "nodes", {}).keys())
        rels = list(getattr(onto, "relationships", {}).keys())
        return (base + f"\nLabels: {labels}\nRelationship types: {rels}\n"
                "Every matched node must carry the workspace scope, and include "
                "a LIMIT passed as $limit.")
    schema = schema_for_prompt(onto, policy)
    block = ("\nSchema (use only these labels, relationship types, properties "
             "and parameters):\n" + json.dumps(schema, indent=2, default=str) +
             "\nRules:\n- Every matched node must be workspace-scoped.\n"
             "- Include a LIMIT passed as the $limit parameter.")
    if composition == "full":
        return base + block
    # full_examples: two worked examples in the EXACT contract-conformant form —
    # inline-map scope {_workspace_id: $workspace_id} + LIMIT $limit. This is the
    # ICL move: show the precise syntax the contract requires, not just describe.
    examples = (
        "\n\nExamples (copy this exact form):\n"
        "Q: How many X nodes? -> "
        "MATCH (n:X {_workspace_id: $workspace_id}) RETURN count(n) AS v "
        "LIMIT $limit   with params_json {\"limit\": 1}\n"
        "Q: How many distinct A that R a B? -> "
        "MATCH (a:A {_workspace_id: $workspace_id})-[:R]->(:B) "
        "RETURN count(DISTINCT a) AS v LIMIT $limit   with params_json {\"limit\": 1}")
    return base + block + examples


def _make_tool(store, database, policy, mode, recorder):
    from agents import function_tool

    from seocho.query.workload_compiler import validate_text2cypher_fallback

    @function_tool(
        name_override="run_cypher",
        description_override=("Run one read-only Cypher query and return rows as "
                              "JSON. Pass params as params_json, e.g. "
                              "{\"limit\": 1}. Include a LIMIT."))
    def run_cypher(cypher: str, params_json: str = "{}") -> str:
        try:
            params = json.loads(params_json or "{}")
            if not isinstance(params, dict):
                params = {}
        except (TypeError, ValueError):
            params = {}
        # Pin the workspace like the real OS execute_query — the operator's
        # value, not the model's. The model still must WRITE the scope
        # expression in the Cypher; we only supply the parameter value.
        params["workspace_id"] = "default"
        violations = tuple(validate_text2cypher_fallback(
            cypher, params=params, policy=policy))
        recorder.append({"cypher": cypher, "conformant": not violations,
                         "violations": list(violations)})
        if mode == "hard" and violations:
            return json.dumps({"error": "schema_violation",
                               "message": "the query violates the graph schema: "
                               + ", ".join(violations) + ". Re-emit using only "
                               "declared labels/relationship types/parameters."})
        driver = getattr(store, "_driver", None) or getattr(store, "driver", None)
        from neo4j.exceptions import Neo4jError
        try:
            with driver.session(database=database,
                                default_access_mode="READ") as s:
                rows = [dict(r) for _, r in zip(range(1000), s.run(cypher, **params))]
        except Neo4jError as exc:
            return json.dumps({"error": exc.code, "message": str(exc)[:200]})
        return json.dumps({"rows": rows, "row_count": len(rows)}, default=str)

    return run_cypher


def _answer_has(number, text):
    forms = {str(number), f"{number:,}"}
    return bool(forms & set(re.findall(r"[0-9][0-9,]*", text)))


async def _run_one(onto, store, database, policy, model, composition, mode, q,
                   max_turns):
    from agents import Agent, ModelSettings, Runner, set_tracing_disabled

    from seocho.integrations.openai_agents import _mara_model
    set_tracing_disabled(True)
    recorder: List[Dict[str, Any]] = []
    tool = _make_tool(store, database, policy, mode, recorder)
    agent = Agent(name=f"{composition}_{mode}", instructions=_instruction(composition, onto),
                  model=_mara_model(model),
                  model_settings=ModelSettings(temperature=0.0), tools=[tool])
    try:
        result = await Runner.run(agent, q["q"], max_turns=max_turns)
        text = str(result.final_output or "")
        toks = sum(int(getattr(getattr(r, "usage", None), "total_tokens", 0) or 0)
                   for r in getattr(result, "raw_responses", []) or [])
        err = None
    except Exception as exc:
        text, toks, err = "", 0, f"{type(exc).__name__}: {exc}"
    return {"queries": recorder, "tokens": toks, "turns": len(recorder),
            "correct": _answer_has(q["gold"], text), "error": err}


async def run(container, uri, database, models, ontology_path, max_turns):
    from seocho.ontology import Ontology
    from seocho.query.hybrid_planner import policy_from_ontology
    from seocho.store.graph import Neo4jGraphStore

    u, p = auth_of(container)
    gold = compute_gold(uri, u, p, database)
    for q in _Q:
        q["gold"] = gold[q["id"]]
    onto = Ontology.from_yaml(ontology_path)
    policy = policy_from_ontology(onto)
    store = Neo4jGraphStore(uri, u, p)

    report = {"database": database, "models": models, "gold": gold, "by_model": {}}
    for model in models:
        cfgs = {}
        for comp, mode in _CONFIGS:
            per_q = []
            for q in _Q:
                r = await _run_one(onto, store, database, policy, model, comp,
                                   mode, q, max_turns)
                per_q.append({"id": q["id"], "cat": q["cat"], **r})
            emitted = [qq for c in per_q for qq in c["queries"]]
            conformant = sum(1 for e in emitted if e["conformant"])
            cfgs[f"{comp}/{mode}"] = {
                "composition": comp, "mode": mode,
                "correct": sum(c["correct"] for c in per_q), "n": len(per_q),
                "emitted_queries": len(emitted),
                "conformant_queries": conformant,
                "conformance_rate": round(conformant / len(emitted), 3) if emitted else None,
                "drift_events": sum(1 for e in emitted if not e["conformant"]),
                "tokens": sum(c["tokens"] for c in per_q),
                "avg_turns": round(sum(c["turns"] for c in per_q) / len(per_q), 2),
                "cases": per_q,
            }
        report["by_model"][model] = cfgs
    store.close() if hasattr(store, "close") else None
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="finbenchl1")
    ap.add_argument("--models", default="gpt-oss-120b,gemma-4-31B-it")
    ap.add_argument("--ontology",
                    default="examples/finbench/finbench.ontology.yaml")
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    rep = asyncio.run(run(args.container, args.uri, args.database, models,
                          args.ontology, args.max_turns))
    for model, cfgs in rep["by_model"].items():
        print(f"\n=== {model} ({len(_Q)} questions) ===")
        print(f"  {'config':20s} {'correct':>7s} {'conform':>8s} "
              f"{'drift':>6s} {'tokens':>8s} {'turns':>6s}")
        for name, c in cfgs.items():
            print(f"  {name:20s} {str(c['correct'])+'/'+str(c['n']):>7s} "
                  f"{(str(int(c['conformance_rate']*100))+'%') if c['conformance_rate'] is not None else '-':>8s} "
                  f"{c['drift_events']:>6d} {c['tokens']:>8d} {c['avg_turns']:>6.1f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
