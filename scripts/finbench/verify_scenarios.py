#!/usr/bin/env python3
"""Workstream B3/B6: verify the planted AML scenarios and measure plan quality.

Each scenario is run in two query shapes against the same graph, because the gap
between them is the scalability story:

* ``tuned``  — label-qualified with an indexed identifying property
  (``MATCH (a:Account {acct_no: $n})``). Resolves through NodeIndexSeek.
* ``naive``  — the shape an agent writes when it omits the label
  (``MATCH (a)-[...]-> WHERE a.id = $id``). An index on ``:Account(id)`` cannot
  serve an unlabeled pattern, so the planner falls back to AllNodesScan.

Both return identical answers, so accuracy testing cannot tell them apart. Their
cost diverges with graph size:

    dbHits      SF100        SF1000
    tuned          25            25     (constant)
    naive     660,122     6,600,122     (linear in nodes)

At SF1 the two shapes are indistinguishable (~15 ms). That is precisely why a
benchmark that reports only accuracy at small scale factors cannot see the
failure — and why an agent scoring 100% can still be unshippable.

Usage:
    python scripts/finbench/verify_scenarios.py --src outputs/finbench/sf1 \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1 [--out report.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

from neo4j import GraphDatabase

def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


instrumentation = _load("finbench_instrumentation", "instrumentation.py")
protocol = _load("finbench_bench_protocol", "bench_protocol.py")


def _timed(session, cypher: str, **params):
    start = time.perf_counter()
    rows = [r.data() for r in session.run(cypher, **params)]
    return rows, (time.perf_counter() - start) * 1000.0


def _profiled(session, cypher: str, **params) -> dict:
    result = session.run("PROFILE " + cypher, **params)
    rows = [r.data() for r in result]
    plan = instrumentation.summarize_plan(result.consume().profile)
    plan["result_rows"] = len(rows)
    return plan


# (name, tuned cypher, naive cypher, param builder from gold)
def _scenarios(gold: dict) -> list[tuple]:
    flagged_no = int(gold["flagged_account"])
    flagged_id = f"Account:{flagged_no}"
    cycle = gold["scenario_5_laundering_cycles"]["cycles"][0]
    hub_no = int(gold["scenario_5_fan_in_smurfing"]["hub"])

    return [
        (
            "s1_nhop_from_flagged",
            ("MATCH (a:Account {acct_no: $no})-[:TRANSFER*1..3]->(b:Account) "
             "RETURN DISTINCT b.acct_no AS item ORDER BY item", {"no": flagged_no}),
            ("MATCH (a)-[:TRANSFER*1..3]->(b) WHERE a.id = $id "
             "RETURN DISTINCT b.id AS item ORDER BY item", {"id": flagged_id}),
            sorted(gold["scenario_1_nhop_from_flagged"]["within_3_hops"]),
        ),
        (
            "s5_laundering_cycle",
            ("MATCH p=(a:Account {acct_no: $no})-[:TRANSFER*3..3]->(a) "
             "RETURN [n IN nodes(p) | n.acct_no] AS item LIMIT 1", {"no": int(cycle[0])}),
            ("MATCH p=(a)-[:TRANSFER*3..3]->(a) WHERE a.id = $id "
             "RETURN [n IN nodes(p) | n.id] AS item LIMIT 1", {"id": f"Account:{cycle[0]}"}),
            cycle,
        ),
        (
            "s5_fan_in_smurfing",
            ("MATCH (src:Account)-[:TRANSFER]->(h:Account {acct_no: $no}) "
             "RETURN count(DISTINCT src) AS item", {"no": hub_no}),
            ("MATCH (src)-[:TRANSFER]->(h) WHERE h.id = $id "
             "RETURN count(DISTINCT src) AS item", {"id": f"Account:{hub_no}"}),
            gold["scenario_5_fan_in_smurfing"]["sender_count"],
        ),
    ]


def verify(src: Path, uri: str, user: str, password: str, database: str,
           *, repeats: int = 10, warm: bool = True, container: str = "graphrag-neo4j") -> dict:
    gold = json.loads((src / "gold.json").read_text())
    driver = GraphDatabase.driver(uri, auth=(user, password))
    scenarios: list[dict] = []
    try:
        # Neo4j's page cache is empty at startup, so an unwarmed measurement
        # reports I/O rather than query cost. Warm before timing anything.
        environment = protocol.capture_environment(driver, database, container=container)
        environment["page_cache_recommendation"] = protocol.recommend_page_cache(
            int(environment.get("store_bytes") or 0) or None)
        environment["warm_up"] = protocol.warm_up(driver, database) if warm else {"method": "skipped"}
        with driver.session(database=database) as session:
            counts, _ = _timed(session, "MATCH (n) RETURN count(n) AS n")
            edges, _ = _timed(session, "MATCH ()-[r:TRANSFER]->() RETURN count(r) AS r")

            for name, tuned, naive, expected in _scenarios(gold):
                entry: dict = {"name": name}
                for shape, (cypher, params) in (("tuned", tuned), ("naive", naive)):
                    timing = protocol.measure(session, cypher, params, repeats=repeats)
                    plan = _profiled(session, cypher, **params)
                    entry[shape] = {
                        # p50 of the warm repetitions, not a single shot
                        "latency_ms": timing["warm_ms"]["p50"],
                        "cold_ms": timing["cold_ms"],
                        "p95_ms": timing["warm_ms"]["p95"],
                        "p99_ms": timing["warm_ms"]["p99"],
                        "cold_warm_ratio": timing["cold_warm_ratio"],
                        "repeats": timing["repeats"],
                        "db_hits": plan.get("db_hits"),
                        "sargable": plan.get("sargable"),
                        "scans": plan.get("scans", [])[:2],
                        "rows": timing["rows"],
                    }
                # Correctness is judged on the tuned shape; both return the same answer.
                got = [r.get("item") for r in _timed(session, tuned[0], **tuned[1])[0]]
                if name == "s5_fan_in_smurfing":
                    entry["passed"] = bool(got) and int(got[0]) >= int(expected)
                elif name == "s5_laundering_cycle":
                    ring = got[0] if got else []
                    entry["passed"] = len(ring) == len(expected) + 1
                else:
                    entry["passed"] = sorted(int(x) for x in got) == sorted(int(x) for x in expected)
                entry["latency_ms"] = entry["tuned"]["latency_ms"]  # curve compatibility
                # The headline number: how much the plan shape costs at this scale.
                naive_hits = entry["naive"]["db_hits"] or 0
                tuned_hits = entry["tuned"]["db_hits"] or 1
                entry["naive_cost_multiple"] = round(naive_hits / max(tuned_hits, 1), 1)
                scenarios.append(entry)
    finally:
        driver.close()

    return {
        "schema_version": "seocho.finbench.scenario-verify.v3",
        "database": database,
        "environment": environment,
        "protocol": {"repeats": repeats, "warmed": warm,
                     "note": "latency_ms is the p50 of warm repetitions; dbHits is warm-up independent"},
        "graph": {"nodes": counts[0]["n"], "transfers": edges[0]["r"]},
        "scenarios": scenarios,
        "passed": all(x["passed"] for x in scenarios),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--repeats", type=int, default=10, help="warm repetitions per query")
    parser.add_argument("--no-warmup", action="store_true", help="skip page-cache warm-up (not recommended)")
    parser.add_argument("--container", default="graphrag-neo4j")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = verify(args.src, args.uri, args.user, args.password, args.database,
                    repeats=args.repeats, warm=not args.no_warmup, container=args.container)
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
