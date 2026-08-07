#!/usr/bin/env python3
"""Which questions belong to the template catalog, and which to validated generation?

SEOCHO deliberately assembles Cypher from intent + ontology rather than asking the
model to write it (see the CypherBuilder docstring and ADR-0097's pattern catalog,
which carries per-pattern cost hints and alternatives). That buys determinism,
bounded cost, and auditability, and it is not what this experiment disputes.

What the SF1000 runs did show is a *coverage* gap. The catalog's nine patterns
target entity lookup, one-hop relationships and financial metrics; the AML
questions here also need variable-length hops, edge-property filters, and
edge-property projection. When a question falls outside the catalog the template
still answers — with a plausible row count and the wrong content, which is the
hardest failure to detect.

SEOCHO already contains the complement: ``generate_validated_cypher`` in
query/text2cypher.py generates a read query, rejects unknown labels/relationships
and unbounded paths, forces tenant scope and LIMIT, and EXPLAINs before executing.
It is referenced only in ``__all__`` — written and never wired in.

So this measures the two arms on the same questions and the same S1-S5 axes:

  template   DeterministicQueryPlanner -> pattern catalog
  generated  generate_validated_cypher -> validated, EXPLAIN-gated Cypher

The output is a routing table: per question type, which arm answers correctly and
at what plan cost. That table is the input to the precedence decision (seocho-4bi),
rather than replacing one arm with the other on a hunch.

Usage:
    python scripts/finbench/arm_template_vs_generated.py \
        --ontology examples/finbench/finbench.ontology.yaml \
        --cases examples/finbench/cases.json \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1000 --model gpt-oss-120b \
        --out outputs/finbench/sf1000/arm_comparison.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Dict

from neo4j import GraphDatabase

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


instrumentation = _load("finbench_instrumentation", "instrumentation.py")


def _policy_from_ontology(ontology: Any):
    """Derive the generation guardrail from the ontology.

    The policy is the ontology restated as limits: only declared labels and
    relationships may appear, paths must be bounded, tenant scope and a row budget
    are mandatory. That is the same information the templates consume, which keeps
    the two arms comparable rather than one being handed a looser contract.
    """
    from seocho.query.workload_compiler import Text2CypherFallbackPolicy

    properties = {"workspace_id", "limit"}
    for node in ontology.nodes.values():
        properties.update(getattr(node, "properties", {}) or {})
    for rel in ontology.relationships.values():
        properties.update(getattr(rel, "properties", {}) or {})
    # The tenant property must name what the graph actually stores. SEOCHO writes
    # `_workspace_id`, and leaving the policy on its `workspace_id` default made the
    # validator reject every generated query for missing tenant scope — a
    # configuration error that would otherwise read as "generation cannot do this".
    properties.add("_workspace_id")
    return Text2CypherFallbackPolicy(
        allowed_labels=tuple(sorted(ontology.nodes)),
        allowed_relationships=tuple(sorted(ontology.relationships)),
        allowed_properties=tuple(sorted(properties)),
        workspace_property="_workspace_id",
        max_graph_hops=4,
        max_result_rows=50,
        max_repair_attempts=1,
    )


def _schema_for_prompt(ontology: Any, policy: Any) -> Dict[str, tuple]:
    """Label -> property names, plus relationship endpoints as pseudo-entries.

    The tenant convention is stated explicitly. Without it the model invented a
    ``Workspace`` node with a ``HAS_ACCOUNT`` edge to satisfy the scope requirement —
    the guardrail correctly rejected that, but the prompt had not said how scoping
    is expressed, so the rejection measured the prompt rather than the model.
    """
    schema: Dict[str, tuple] = {
        "__tenant_scope__": (
            f"every matched node must carry {{{policy.workspace_property}: $workspace_id}} "
            "inline in its pattern; do not introduce a workspace node or relationship",
        ),
    }
    schema.update({
        label: tuple(sorted(getattr(node, "properties", {}) or {}))
        for label, node in ontology.nodes.items()
    })
    for rtype, rel in ontology.relationships.items():
        schema[f"({rel.source})-[:{rtype}]->({rel.target})"] = tuple(
            sorted(getattr(rel, "properties", {}) or {})
        )
    return schema


def _run_template_arm(case: dict, ontology: Any, driver: Any, database: str,
                      model: str, workspace_id: str) -> dict:
    from seocho.query.planner import DeterministicQueryPlanner
    from seocho.store.llm import create_llm_backend

    planner = DeterministicQueryPlanner(
        ontology=ontology,
        llm=create_llm_backend(provider="mara", model=model),
        workspace_id=workspace_id,
    )
    start = time.perf_counter()
    try:
        plan = planner.plan(case["question"])
    except Exception as exc:
        return {"arm": "template", "error": f"{type(exc).__name__}: {exc}"[:200],
                "latency_ms": (time.perf_counter() - start) * 1000}
    latency = (time.perf_counter() - start) * 1000
    return _execute_and_score(case, plan.cypher, dict(plan.params or {}), driver,
                              database, "template", latency,
                              intent=(plan.intent_data or {}).get("intent"),
                              generation_attempts=1, error=plan.error)


def _run_generated_arm(case: dict, ontology: Any, driver: Any, database: str,
                       model: str, workspace_id: str) -> dict:
    from seocho.query.text2cypher import generate_validated_cypher
    from seocho.store.llm import create_llm_backend

    backend = create_llm_backend(provider="mara", model=model)
    policy = _policy_from_ontology(ontology)
    schema = _schema_for_prompt(ontology, policy)
    params = {"workspace_id": workspace_id, "limit": policy.max_result_rows}

    async def explain(cypher: str, explain_params: Any) -> None:
        # The generation path must prove the query plans before it runs; failures
        # come back as feedback for its single repair attempt.
        with driver.session(database=database) as session:
            session.run("EXPLAIN " + cypher, **dict(explain_params)).consume()

    start = time.perf_counter()
    try:
        result = asyncio.run(generate_validated_cypher(
            question=case["question"],
            schema=schema,
            params=params,
            policy=policy,
            backend=backend,
            model=model,
            explain=explain,
        ))
    except Exception as exc:
        # A rejection is a legitimate outcome: fail-closed rather than run an
        # unvalidated query. Recorded so the routing table can count it.
        return {"arm": "generated", "rejected": True,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "latency_ms": (time.perf_counter() - start) * 1000,
                "correct": False}
    latency = (time.perf_counter() - start) * 1000
    return _execute_and_score(case, result.cypher, dict(result.params), driver,
                              database, "generated", latency, intent="generated",
                              generation_attempts=result.attempts, error=None)


def _execute_and_score(case: dict, cypher: str, params: dict, driver: Any,
                       database: str, arm: str, latency_ms: float, *,
                       intent: Any, generation_attempts: int, error: Any) -> dict:
    row: Dict[str, Any] = {
        "arm": arm, "id": case["id"], "intent": intent,
        "generation_attempts": generation_attempts,
        "latency_ms": latency_ms, "cypher": (cypher or "")[:600], "error": error,
    }
    if error or not cypher:
        row.update({"correct": False, "rows": 0})
        return row
    try:
        with driver.session(database=database) as session:
            result = session.run(cypher, **params)
            records = [r.data() for r in result]
        plan = instrumentation.profile_cypher(driver, cypher, params, database)
    except Exception as exc:
        row.update({"correct": False, "rows": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:200]})
        return row

    # Scored on the rows themselves: this compares query construction, so the
    # synthesis step is deliberately excluded from both arms.
    rendered = json.dumps(records, default=str, ensure_ascii=False)
    score = instrumentation.score_answer(rendered, case.get("gold") or [])
    row.update({
        "rows": len(records), "correct": score["correct"], "recall": score["recall"],
        "precision": score["precision"],
        "db_hits": plan.get("db_hits"), "sargable": plan.get("sargable"),
        "scans": plan.get("scans", [])[:2],
    })
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from seocho.ontology import Ontology
    ontology = Ontology.load(args.ontology)
    cases = json.loads(args.cases.read_text())["cases"]
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    rows = []
    try:
        for case in cases:
            for runner, arm in ((_run_template_arm, "template"), (_run_generated_arm, "generated")):
                print(f"[arms] {case['id']} :: {arm}", flush=True)
                rows.append(runner(case, ontology, driver, args.database,
                                   args.model, args.workspace_id))
    finally:
        driver.close()

    by_arm: Dict[str, list] = {}
    for row in rows:
        by_arm.setdefault(row["arm"], []).append(row)

    def summary(arm_rows: list) -> dict:
        n = len(arm_rows) or 1
        profiled = [r for r in arm_rows if r.get("db_hits") is not None]
        return {
            "cases": len(arm_rows),
            "accuracy": sum(1 for r in arm_rows if r.get("correct")) / n,
            "rejected": sum(1 for r in arm_rows if r.get("rejected")),
            "errors": sum(1 for r in arm_rows if r.get("error")),
            "sargable_rate": (sum(1 for r in profiled if r.get("sargable")) / len(profiled)) if profiled else None,
            "db_hits_total": sum(int(r.get("db_hits") or 0) for r in profiled),
        }

    report = {
        "schema_version": "seocho.finbench.arm-comparison.v1",
        "database": args.database, "model": args.model,
        "summary": {arm: summary(arm_rows) for arm, arm_rows in by_arm.items()},
        "rows": rows,
    }

    lines = ["# Template catalog vs validated generation", "",
             f"database `{args.database}` · model `{args.model}` · scored on returned rows "
             "(synthesis excluded from both arms)", "",
             "| arm | accuracy | rejected | errors | sargable | dbHits total |",
             "|---|---|---|---|---|---|"]
    for arm in ("template", "generated"):
        s = report["summary"].get(arm)
        if not s:
            continue
        sarg = "n/a" if s["sargable_rate"] is None else f"{s['sargable_rate']:.0%}"
        lines.append(f"| {arm} | {s['accuracy']:.0%} | {s['rejected']} | {s['errors']} | "
                     f"{sarg} | {s['db_hits_total']:,} |")
    lines += ["", "## Per question — which arm should own it", "",
              "| question | template | generated |", "|---|---|---|"]
    for case in cases:
        cells = []
        for arm in ("template", "generated"):
            r = next((x for x in rows if x["arm"] == arm and x.get("id") == case["id"]), None)
            if r is None:
                cells.append("—")
            elif r.get("rejected"):
                cells.append("⊘ rejected")
            elif r.get("correct"):
                cells.append(f"✓ {r.get('db_hits', '?')} hits")
            else:
                cells.append(f"✗ rows={r.get('rows', 0)}")
        lines.append(f"| {case['id']} | {cells[0]} | {cells[1]} |")
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
