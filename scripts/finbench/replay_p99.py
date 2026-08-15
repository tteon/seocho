"""Replay the query each agent design settled on, and measure its latency distribution.

Stage two of the interaction experiment. Stage one runs the agent and records the Cypher it
finally executed; this script takes that query, runs it N times with no model in the loop, and
reports p50 / p95 / p99.

Why the two stages are separate. A p99 needs on the order of a hundred samples per cell, and
144 cells of a hundred LLM episodes each is fourteen thousand model calls to estimate a number
the model is not even part of. It is also the wrong number: once an agent design ships, the
query it produces is fixed, and what an operator is on the hook for is that query's tail under
load. So stage one measures how the agent *behaves* — round trips, db hits, whether it gets the
answer right — with a handful of repeats, and stage two measures what it *costs* with enough
samples for a tail.

Two latencies are reported and they answer different questions:

  ``server_ms``  the database's own view, from the query summary. Excludes the driver, the
                 network and the harness. This is the number that scales with the graph.
  ``client_ms``  wall clock around the call including result materialisation. Always larger;
                 the gap is the driver overhead measured separately at 5.1x decode cost.

The first iteration of each query is discarded. Neo4j compiles and caches a plan on first
execution, so including it would report a compile in the tail of every cell and make the cheap
cells look as variable as the expensive ones.

Usage:
  python scripts/finbench/replay_p99.py --password "$PW" \
      --episodes outputs/finbench/agent_interaction.json \
      --iterations 100 --out outputs/finbench/replay_p99.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

WS = "default"
ROW_CAP = 50


def scored(episodes):
    """Episodes that can be scored at all.

    An episode whose reference query never completed has `score_correct = None`. Counting it as
    a failure would charge the agent for the database's limit, and counting it as a success
    would be worse; it is excluded from the numerator and the denominator both, and the count
    of exclusions is reported separately.
    """
    return [e for e in episodes if e.get("score_correct") is not None]

def quantile(sorted_values: List[float], q: float) -> float:
    """Nearest-rank quantile.

    Deliberately not an interpolating estimator: p99 of 100 samples should be the 99th
    observed value, not a number that never occurred. Interpolation invents a value below every
    sample that motivated looking at the tail in the first place.
    """
    if not sorted_values:
        return float("nan")
    k = max(0, min(len(sorted_values) - 1, int(round(q * len(sorted_values) + 0.5)) - 1))
    return sorted_values[k]


def replay(driver, database: str, cypher: str, params: Dict[str, Any], *,
           iterations: int, timeout_s: float, cell_budget_s: float = 90.0,
           min_iterations: int = 7) -> Dict[str, Any]:
    """Sample until the iteration count or the time budget runs out, whichever comes first.

    A fixed hundred iterations is affordable for a 6 ms query and not for a 20-second one: the
    unanchored aggregates at SF100 would take half an hour per cell, and there are forty-eight
    of them. The budget keeps the cheap cells at a hundred samples, where a nearest-rank p99 is
    meaningful, and reports the reduced sample count on the expensive ones rather than quietly
    thinning them. `iterations` is on every row for exactly that reason — a p99 drawn from
    seven samples is the seventh value and should be read as such.
    """
    client: List[float] = []
    server: List[float] = []
    rows_seen = 0
    failures = 0
    error: Optional[str] = None

    started = time.perf_counter()
    with driver.session(database=database) as session:
        for i in range(iterations + 1):  # +1: the discarded compile
            if i > min_iterations and (time.perf_counter() - started) > cell_budget_s:
                break
            t0 = time.perf_counter()
            tx = session.begin_transaction(timeout=timeout_s)
            try:
                result = tx.run(cypher, **params)
                rows = [dict(r) for _, r in zip(range(ROW_CAP), result)]
                summary = result.consume()
                tx.commit()
            except Neo4jError as exc:
                tx.close()
                failures += 1
                error = error or f"{exc.code}"
                if failures >= 3:
                    break
                continue
            except Exception as exc:
                tx.close()
                failures += 1
                error = error or type(exc).__name__
                if failures >= 3:
                    break
                continue
            elapsed = (time.perf_counter() - t0) * 1000
            if i == 0:
                continue
            client.append(elapsed)
            rows_seen = len(rows)
            avail = summary.result_available_after or 0
            consumed = summary.result_consumed_after or 0
            server.append(float(avail + consumed))

    if not client:
        return {"ok": False, "error": error or "no successful iteration",
                "iterations": 0, "failures": failures}
    client.sort()
    server.sort()
    return {
        "ok": True, "iterations": len(client), "failures": failures, "rows": rows_seen,
        "budget_exhausted": len(client) < iterations,
        "client_p50": round(quantile(client, 0.50), 2),
        "client_p95": round(quantile(client, 0.95), 2),
        "client_p99": round(quantile(client, 0.99), 2),
        "client_max": round(client[-1], 2),
        "server_p50": round(quantile(server, 0.50), 2),
        "server_p95": round(quantile(server, 0.95), 2),
        "server_p99": round(quantile(server, 0.99), 2),
        "server_max": round(server[-1], 2),
        "client_mean": round(statistics.fmean(client), 2),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", required=True)
    p.add_argument("--episodes", default="outputs/finbench/agent_interaction.json")
    p.add_argument("--iterations", type=int, default=100)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--cell-budget", type=float, default=90.0,
                   help="wall-clock seconds per cell; a cell stops early once this is spent, "
                        "and the sample count it reached is reported on the row")
    p.add_argument("--out", default="outputs/finbench/replay_p99.json")
    args = p.parse_args()

    run = json.loads(Path(args.episodes).read_text())
    context = run.get("context", {})

    # One cell is one (scale, question, arm). Repeats of the same cell usually settle on the
    # same query at temperature 0; where they do not, the most common one is replayed and the
    # disagreement is reported, because an agent design that produces two different queries for
    # one question has two different tails and the operator should know.
    cells: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for e in run["episodes"]:
        cells[(e["sf"], e["database"], e["question_id"], e["arm"])].append(e)

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    out: List[Dict[str, Any]] = []
    for (sf, database, qid, arm), episodes in sorted(cells.items()):
        queries = [e["settled_cypher"] for e in episodes if e.get("settled_cypher")]
        variants = len(set(queries))
        if not queries:
            out.append({"sf": sf, "database": database, "question_id": qid, "arm": arm,
                        "ok": False, "error": "the agent never executed a query",
                        "variants": 0,
                        "correct_rate": (sum(e["score_correct"] for e in scored(episodes))
                                          / len(scored(episodes))
                                          if scored(episodes) else None)})
            print(f"  sf{sf:<4} {arm:9s} {qid:11s}  no query executed", flush=True)
            continue
        cypher = Counter(queries).most_common(1)[0][0]
        anchor = context.get(database, {}).get("anchor")
        params = {"workspace_id": WS, "ws": WS, "limit": ROW_CAP}
        if anchor is not None:
            params["a"] = anchor
            params["acct_no"] = anchor
        stats = replay(driver, database, cypher, params,
                       iterations=args.iterations, timeout_s=args.timeout,
                       cell_budget_s=args.cell_budget)
        row = {
            "sf": sf, "database": database, "question_id": qid, "arm": arm,
            "audience": episodes[0]["audience"], "difficulty": episodes[0]["difficulty"],
            "variants": variants,
            "correct_rate": (round(sum(e["score_correct"] for e in scored(episodes))
                                   / len(scored(episodes)), 3)
                             if scored(episodes) else None),
            "round_trips_median": statistics.median(e["round_trips"] for e in episodes),
            "db_hits_median": statistics.median(e["db_hits"] for e in episodes),
            "cypher": cypher,
            **stats,
        }
        out.append(row)
        if stats["ok"]:
            print(f"  sf{sf:<4} {arm:9s} {qid:11s}  p50={stats['server_p50']:>9.1f} "
                  f"p95={stats['server_p95']:>9.1f} p99={stats['server_p99']:>9.1f} ms "
                  f"(server) n={stats['iterations']:<3} variants={variants} "
                  f"correct={row['correct_rate']}", flush=True)
        else:
            print(f"  sf{sf:<4} {arm:9s} {qid:11s}  FAILED {stats['error']}", flush=True)
    driver.close()

    payload = {
        "schema_version": "seocho.finbench.replay-p99.v1",
        "iterations": args.iterations, "warmup_discarded": 1,
        "cell_budget_s": args.cell_budget,
        "source": args.episodes, "model": run.get("model"),
        "note": ("server_* comes from the query summary (available_after + consumed_after) and "
                 "excludes driver and network; client_* is wall clock around the call. "
                 "Quantiles are nearest-rank."),
        "cells": out,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1, default=str))
    print(f"\nwrote {args.out}  ({len(out)} cells)")


if __name__ == "__main__":
    main()
