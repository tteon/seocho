#!/usr/bin/env python3
"""Same AML questions, two paradigms: text2SQL over DuckDB vs text2Cypher over DozerDB.

The graph arm answered 25% of the analyst-grade AML scenarios. That number alone invites
the wrong reading — "graph-RAG is weak" — when most of those questions are aggregations
over a time window, which is what SQL was built for. The interesting question is not
which paradigm wins overall but **which questions each one can express at all**.

The comparison is close to free because the data already exists in both forms: the
DuckDB Parquet snapshot is the same source the graph was loaded from, so both arms read
identical rows with identical planted patterns and identical gold answers.

Fairness measures, since an unfair version of this is easy to build by accident:

* Both arms receive the same schema knowledge, derived from the same source — the graph
  arm from the ontology, the SQL arm from the table/column inventory.
* Both are scored by the same function over the rows their query returns, with synthesis
  excluded, so this measures query construction rather than answer writing.
* Both are read-only and row-bounded.

What is *not* comparable, and is reported separately rather than merged: plan-cost
units. Neo4j db hits count storage accesses; DuckDB is columnar and vectorized, so its
natural unit is rows scanned. Presenting one number for both would be false precision.

Usage:
    python scripts/finbench/arm_sql_vs_cypher.py \
        --src outputs/finbench/sf1 --cases examples/finbench/cases_aml.json \
        --ontology examples/finbench/finbench.ontology.yaml \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1 --model gpt-oss-120b \
        --out outputs/finbench/sf1/sql_vs_cypher.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import duckdb
from neo4j import GraphDatabase

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


instrumentation = _load("finbench_instrumentation", "instrumentation.py")

# Tables the SQL arm may read, mapped to the snapshot's Parquet files. This is the
# relational counterpart of the ontology's declared labels.
_TABLES = {
    "account": "nodes/Account.parquet",
    "person": "nodes/Person.parquet",
    "company": "nodes/Company.parquet",
    "loan": "nodes/Loan.parquet",
    "channel": "nodes/Channel.parquet",
    "transfer": "edges/transfer.parquet",
    "own": "edges/own.parquet",
    "deposit": "edges/deposit.parquet",
    "repay": "edges/repay.parquet",
    "uses_channel": "edges/uses_channel.parquet",
}

_WRITE_TOKENS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|copy|export|install|load|pragma)\b",
    re.IGNORECASE,
)


def _register(src: Path) -> duckdb.DuckDBPyConnection:
    """Views over the snapshot, so the SQL arm reads exactly the graph's source rows."""
    con = duckdb.connect()
    for table, rel in _TABLES.items():
        path = src / rel
        if path.exists():
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    return con


def _sql_schema(con: duckdb.DuckDBPyConnection) -> Dict[str, List[str]]:
    schema: Dict[str, List[str]] = {}
    for table in _TABLES:
        try:
            cols = con.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()
            schema[table] = [c[0] for c in cols]
        except Exception:
            continue
    return schema


def _validate_sql(sql: str, *, max_rows: int) -> List[str]:
    """Read-only and row-bounded — the SQL analogue of the Cypher guardrail."""
    violations: List[str] = []
    if not sql.strip():
        return ["empty"]
    if _WRITE_TOKENS.search(sql):
        violations.append("not_read_only")
    if "limit" not in sql.lower():
        violations.append("missing_limit")
    else:
        found = re.search(r"limit\s+(\d+)", sql, re.IGNORECASE)
        if found and int(found.group(1)) > max_rows:
            violations.append("result_limit_exceeded")
    return violations


def _run_sql_arm(case: dict, con: duckdb.DuckDBPyConnection, schema: Dict[str, List[str]],
                 backend: Any, model: str, *, max_rows: int, repairs: int = 1) -> dict:
    """Generate SQL, validate, EXPLAIN, execute — mirroring the Cypher generation arm."""
    system = (
        "You write one read-only DuckDB SQL query. Return a JSON object with key `sql`. "
        "Use only the supplied tables and columns. Include a LIMIT. "
        "Timestamps (`ts`) are unix epoch seconds; amounts are integers. "
        "Node ids are plain integers in these tables (no label prefix)."
    )
    feedback: List[str] = []
    start = time.perf_counter()
    sql = ""
    for attempt in range(1, repairs + 2):
        response = backend.complete(
            system=system,
            user=json.dumps({"question": case["question"], "schema": schema,
                             "max_rows": max_rows, "prior_failures": feedback},
                            sort_keys=True),
            temperature=0.0,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        try:
            sql = str(response.json().get("sql", "")).strip()
        except Exception as exc:
            feedback = [f"invalid_json:{type(exc).__name__}"]
            continue
        violations = _validate_sql(sql, max_rows=max_rows)
        if violations:
            feedback = violations
            continue
        try:
            con.execute("EXPLAIN " + sql).fetchall()
        except Exception as exc:
            feedback = [f"explain_failed:{type(exc).__name__}"]
            continue
        break
    else:
        return {"arm": "sql", "id": case["id"], "rejected": True, "correct": False,
                "error": "sql rejected: " + ",".join(feedback),
                "latency_ms": (time.perf_counter() - start) * 1000, "attempts": attempt}

    row: Dict[str, Any] = {"arm": "sql", "id": case["id"], "attempts": attempt,
                           "query": sql[:600]}
    try:
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description] if con.description else []
        records = [dict(zip(cols, r)) for r in rows]
        # DuckDB's comparable cost unit is rows scanned, not storage accesses.
        plan = con.execute("EXPLAIN ANALYZE " + sql).fetchall()
        plan_text = "\n".join(str(p[-1]) for p in plan)
        scanned = sum(int(x) for x in re.findall(r"(\d+)\s+Rows", plan_text)) or None
        row.update({"rows": len(records), "rows_scanned": scanned,
                    "seq_scan": "SEQ_SCAN" in plan_text.upper()})
    except Exception as exc:
        row.update({"correct": False, "rows": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "latency_ms": (time.perf_counter() - start) * 1000})
        return row

    score = instrumentation.score_answer(
        json.dumps(records, default=str, ensure_ascii=False), case.get("gold") or [])
    row.update({"latency_ms": (time.perf_counter() - start) * 1000,
                "correct": score["correct"], "recall": score["recall"], "error": None})
    return row


def _run_cypher_arm(case: dict, ontology: Any, driver: Any, database: str,
                    llm: Any, workspace_id: str) -> dict:
    """The graph arm through the routing policy, scored on returned rows."""
    from seocho.query.hybrid_planner import HybridQueryPlanner, build_explain_callback

    planner = HybridQueryPlanner(
        ontology=ontology, llm=llm, workspace_id=workspace_id,
        explain=build_explain_callback_for(driver, database),
        model=str(getattr(llm, "model", "") or ""),
    )
    start = time.perf_counter()
    try:
        plan = planner.plan(case["question"])
    except Exception as exc:
        return {"arm": "cypher", "id": case["id"], "correct": False,
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "latency_ms": (time.perf_counter() - start) * 1000}
    row: Dict[str, Any] = {"arm": "cypher", "id": case["id"],
                           "route": planner.last_route,
                           "query": (plan.cypher or "")[:600],
                           "attempts": 1}
    if plan.error or not plan.cypher:
        row.update({"correct": False, "rows": 0,
                    "error": plan.error or "no_cypher",
                    "latency_ms": (time.perf_counter() - start) * 1000})
        return row
    try:
        with driver.session(database=database) as session:
            records = [r.data() for r in session.run(plan.cypher, **(plan.params or {}))]
        prof = instrumentation.profile_cypher(driver, plan.cypher, plan.params or {}, database)
    except Exception as exc:
        row.update({"correct": False, "rows": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "latency_ms": (time.perf_counter() - start) * 1000})
        return row
    score = instrumentation.score_answer(
        json.dumps(records, default=str, ensure_ascii=False), case.get("gold") or [])
    row.update({"rows": len(records), "db_hits": prof.get("db_hits"),
                "sargable": prof.get("sargable"),
                "latency_ms": (time.perf_counter() - start) * 1000,
                "correct": score["correct"], "recall": score["recall"], "error": None})
    return row


def build_explain_callback_for(driver: Any, database: str):
    async def explain(cypher: str, params: Any) -> None:
        with driver.session(database=database) as session:
            session.run("EXPLAIN " + cypher, **dict(params)).consume()
    return explain


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="snapshot dir (…/sf1)")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from seocho.ontology import Ontology
    from seocho.store.llm import create_llm_backend

    ontology = Ontology.load(args.ontology)
    cases = json.loads(args.cases.read_text())["cases"]
    con = _register(args.src)
    schema = _sql_schema(con)
    llm = create_llm_backend(provider="mara", model=args.model)
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    rows: List[dict] = []
    try:
        for case in cases:
            print(f"[arms] {case['id']} :: sql", flush=True)
            rows.append(_run_sql_arm(case, con, schema, llm, args.model,
                                     max_rows=args.max_rows))
            print(f"[arms] {case['id']} :: cypher", flush=True)
            rows.append(_run_cypher_arm(case, ontology, driver, args.database,
                                        llm, args.workspace_id))
    finally:
        driver.close()
        con.close()

    def summarize(arm: str) -> dict:
        subset = [r for r in rows if r["arm"] == arm]
        n = len(subset) or 1
        return {
            "cases": len(subset),
            "accuracy": sum(1 for r in subset if r.get("correct")) / n,
            "partial": sum(1 for r in subset if 0 < (r.get("recall") or 0) < 1),
            "rejected": sum(1 for r in subset if r.get("rejected")),
            "errors": sum(1 for r in subset if r.get("error")),
        }

    report = {
        "schema_version": "seocho.finbench.sql-vs-cypher.v1",
        "src": str(args.src), "database": args.database, "model": args.model,
        "summary": {"sql": summarize("sql"), "cypher": summarize("cypher")},
        "rows": rows,
    }

    lines = ["# text2SQL (DuckDB) vs text2Cypher (DozerDB) — same AML questions", "",
             f"snapshot `{args.src}` · graph `{args.database}` · model `{args.model}`", "",
             "Scored on returned rows; synthesis excluded from both arms. Plan-cost units",
             "are reported per engine and not merged: db hits count storage accesses,",
             "DuckDB is columnar so its comparable unit is rows scanned.", "",
             "| arm | accuracy | partial | rejected | errors |", "|---|---|---|---|---|"]
    for arm in ("sql", "cypher"):
        s = report["summary"][arm]
        lines.append(f"| {arm} | {s['accuracy']:.0%} | {s['partial']} | "
                     f"{s['rejected']} | {s['errors']} |")
    lines += ["", "## Per question — which paradigm expresses it", "",
              "| typology | SQL | Cypher |", "|---|---|---|"]
    for case in cases:
        cells = []
        for arm in ("sql", "cypher"):
            r = next((x for x in rows if x["arm"] == arm and x.get("id") == case["id"]), None)
            if r is None:
                cells.append("—")
            elif r.get("rejected"):
                cells.append("⊘ rejected")
            elif r.get("correct"):
                cells.append("✓")
            elif (r.get("recall") or 0) > 0:
                cells.append(f"◐ recall {r['recall']:.2f}")
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
