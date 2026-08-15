"""Replay FinBench agent episodes into trace-schema v1 + H0 node sets.

The 702-episode agent_interaction run stored every call's Cypher, so the
whole workload replays against a reloaded graph without a model in the loop:

  KV side   the serialized context each episode actually shipped —
            instructions prefix (reconstructed exactly via the harness's own
            build_instructions) + question + the tool payload JSON per call.
  DB side   per call, a shadow query returns elementIds of every node
            variable the MATCH..WHERE clause binds (the read set — closer to
            what pages must be resident than the returned rows alone), and a
            second shadow restricted to variables the RETURN clause exposes
            outside aggregates (the context-node set: what the LLM can see).

Exclusions are counted, never silent: truncated call Cyphers (stored capped
at 600 chars) fall back to the episode's settled query; shadow rewrites that
fail scope analysis are logged per reason.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

_spec = importlib.util.spec_from_file_location(
    "fb_agent", REPO / "scripts" / "finbench" / "agent_interaction.py")
fb = importlib.util.module_from_spec(_spec)
sys.modules["fb_agent"] = fb
_spec.loader.exec_module(fb)

_tspec = importlib.util.spec_from_file_location(
    "pattern_trace_schema", REPO / "scripts" / "pattern_traces" / "schema.py")
tschema = importlib.util.module_from_spec(_tspec)
sys.modules["pattern_trace_schema"] = tschema
_tspec.loader.exec_module(tschema)

NODE_VAR = re.compile(r"\(\s*(\w+)\s*:")
PATH_VAR = re.compile(r"MATCH\s+(\w+)\s*=", re.I)
AGG = re.compile(r"\b(count|sum|avg|min|max|collect|percentile\w*|stdev\w*)\s*\(", re.I)


def instructions_for(arm: str, ontology_path: Path) -> str:
    import yaml
    from seocho.ontology import Ontology
    from seocho.query.hybrid_planner import policy_from_ontology, schema_for_prompt

    doc = yaml.safe_load(ontology_path.read_text())
    onto = Ontology.from_dict(doc)
    policy = policy_from_ontology(onto)
    schema = fb.labels_only_schema(onto) if arm == "labels" else schema_for_prompt(onto, policy)
    return fb.build_instructions(schema, arm=arm)


def shadow_queries(cypher: str) -> tuple[str | None, str | None, list[str], list[str]]:
    """(read_set_query, ctx_set_query, all_vars, ctx_vars) or Nones on failure."""
    idx = cypher.rfind("RETURN")
    if idx < 0:
        return None, None, [], []
    head, tail = cypher[:idx], cypher[idx:]
    node_vars = list(dict.fromkeys(NODE_VAR.findall(cypher)))
    path_vars = list(dict.fromkeys(PATH_VAR.findall(cypher)))
    node_vars = [v for v in node_vars if v not in path_vars]
    if not node_vars and not path_vars:
        return None, None, [], []
    # WITH clauses change scope; a shadow over the full head would reference
    # out-of-scope vars. Refuse rather than guess.
    if re.search(r"\bWITH\b", head, re.I):
        return None, None, [], []

    def id_exprs(variables: list[str], paths: list[str]) -> str:
        parts = [f"elementId({v}) AS id_{v}" for v in variables]
        parts += [f"[x IN nodes({p}) | elementId(x)] AS ids_{p}" for p in paths]
        return ", ".join(parts)

    read_q = head + "RETURN DISTINCT " + id_exprs(node_vars, path_vars) + "\nLIMIT 500000"

    def mask_aggregates(text: str) -> str:
        # Remove each aggregate CALL including its argument span, so a var
        # that appears only inside count(...)/sum(...) does not count as
        # context-exposed — an aggregate row carries no node into the LLM.
        out = []
        i = 0
        while i < len(text):
            m = AGG.search(text, i)
            if not m:
                out.append(text[i:])
                break
            out.append(text[i:m.start()])
            depth, j = 1, m.end()
            while j < len(text) and depth:
                depth += text[j] == "("
                depth -= text[j] == ")"
                j += 1
            i = j
        return "".join(out)

    exposed = mask_aggregates(tail)
    ctx_vars = [v for v in node_vars + path_vars
                if re.search(rf"\b{re.escape(v)}\b", exposed)]
    ctx_q = None
    if ctx_vars:
        # The context receives only the first row_cap rows, so the context-node
        # shadow is capped by the same $limit the original ran with. The read
        # shadow stays uncapped: the DB touches what the pattern binds, and the
        # gap between the two IS the asymmetry H0 is probing. (Caveat recorded:
        # the shadow drops the original ORDER BY, so *which* row_cap rows is
        # approximate for ordered queries.)
        ctx_q = head + "RETURN DISTINCT " + id_exprs(
            [v for v in ctx_vars if v in node_vars],
            [p for p in ctx_vars if p in path_vars]) + "\nLIMIT $limit"
    return read_q, ctx_q, node_vars + path_vars, ctx_vars


def collect_ids(rows: list[dict]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        for value in row.values():
            if isinstance(value, str):
                out.add(value)
            elif isinstance(value, list):
                out.update(x for x in value if isinstance(x, str))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=str(REPO / "outputs/finbench/agent_interaction.json"))
    parser.add_argument("--uri", default="bolt://localhost:17687")
    parser.add_argument("--password", required=True)
    parser.add_argument("--databases", nargs="+", default=["finbenchl1", "finbenchl10"])
    parser.add_argument("--ontology", default=str(REPO / "examples/finbench/finbench.ontology.yaml"))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    from neo4j import GraphDatabase

    run = json.loads(Path(args.run).read_text())
    episodes = [e for e in run["episodes"] if e["database"] in set(args.databases)]
    prefixes = {arm: instructions_for(arm, Path(args.ontology)) for arm in run["arms"]}
    questions = {q["id"]: q for q in run["questions"]}

    driver = GraphDatabase.driver(args.uri, auth=("neo4j", args.password))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    t0 = time.time()

    trace_path = out_dir / "finbench_traces_v1.jsonl"
    sets_path = out_dir / "h0_sets.jsonl"
    trace_path.write_text("")
    sets_path.write_text("")

    for n, ep in enumerate(episodes):
        db, arm, anchor = ep["database"], ep["arm"], ep.get("anchor")
        row_cap = ep.get("row_cap") or run.get("row_cap", 50)
        params = {"workspace_id": "default", "ws": "default", "limit": row_cap}
        if anchor is not None:
            params["a"] = anchor
            params["acct_no"] = anchor

        q = questions.get(ep["question_id"], {})
        question_text = str(q.get("question", ep["question_id"])).replace(
            "{a}", str(anchor if anchor is not None else ""))
        context_parts = [prefixes[arm], "", "Question: " + question_text, ""]
        read_ids: set[str] = set()
        ctx_ids: set[str] = set()
        episode_v1 = tschema.Episode(
            pattern="single_agent", case_id=f"{db}:{ep['question_id']}:{arm}:{ep.get('repeat', 0)}",
            model=run.get("model", ""), provider="replay")
        episode_v1.workspace_id = "default"
        episode_v1.ontology = "finbench"

        with driver.session(database=db) as session:
            for call in ep["calls"]:
                if call.get("outcome") not in ("ok",):
                    stats["call_skipped_" + str(call.get("outcome"))] += 1
                    continue
                cypher = call["cypher"]
                if len(cypher) >= 600:
                    cypher = ep.get("settled_cypher") or ""
                    stats["call_truncated_fallback"] += 1
                    if not cypher:
                        stats["call_truncated_lost"] += 1
                        continue
                try:
                    result = session.run(cypher, **params)
                    rows = [dict(r) for _, r in zip(range(row_cap), result)]
                    result.consume()
                except Exception as exc:
                    stats["call_replay_error"] += 1
                    stats["err_" + type(exc).__name__] += 1
                    continue
                payload = json.dumps({"rows": rows, "row_count": len(rows),
                                      "row_cap": row_cap}, default=str)
                context_parts.append(payload)
                episode_v1.steps.append(tschema.ToolStep(
                    name="graph_query", rows=len(rows), db_hits=call.get("db_hits", 0),
                    latency_ms=call.get("ms", 0.0)))

                read_q, ctx_q, all_vars, ctx_vars = shadow_queries(cypher)
                if read_q is None:
                    stats["shadow_unsupported"] += 1
                else:
                    try:
                        shadow_rows = [dict(r) for r in session.run(read_q, **params)]
                        read_ids |= collect_ids(shadow_rows)
                        stats["shadow_read_ok"] += 1
                    except Exception:
                        stats["shadow_read_error"] += 1
                    if ctx_q is not None:
                        try:
                            shadow_rows = [dict(r) for r in session.run(ctx_q, **params)]
                            ctx_ids |= collect_ids(shadow_rows)
                            stats["shadow_ctx_ok"] += 1
                        except Exception:
                            stats["shadow_ctx_error"] += 1
                    else:
                        stats["ctx_aggregate_only"] += 1

        context = "\n".join(context_parts)
        episode_v1.outcome = {"round_trips": len(episode_v1.steps),
                              "context_chars": len(context)}
        record = episode_v1.to_dict()
        record["context"] = context
        record["sf"] = ep["sf"]
        with trace_path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        with sets_path.open("a") as fh:
            fh.write(json.dumps({
                "case_id": episode_v1.case_id, "sf": ep["sf"], "arm": arm,
                "question_id": ep["question_id"], "db": db,
                "read_ids": sorted(read_ids), "ctx_ids": sorted(ctx_ids),
            }) + "\n")
        if (n + 1) % 50 == 0:
            print(f"{n+1}/{len(episodes)} episodes, {time.time()-t0:.0f}s, {dict(stats)}",
                  flush=True)

    driver.close()
    print(json.dumps({"episodes": len(episodes), "stats": dict(stats),
                      "elapsed_s": round(time.time() - t0, 1)}, indent=2))


if __name__ == "__main__":
    main()
