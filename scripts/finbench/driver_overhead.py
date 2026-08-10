#!/usr/bin/env python3
"""How much of a query's wall-clock is the driver rather than the server?

This exists to settle one question with a number before any work is done on a native
driver. The graph engine is 0.3-0.9% of end-to-end agent latency in this experiment (122 ms
of engine against 37,083 ms of model time), so "the driver is slow" cannot justify replacing
it. But that is a claim about the *engine's share of the agent*, not about the *driver's
share of the engine*, and the two get conflated. If the driver turns out to be a large
fraction of query time, a fast driver matters for fan-out workloads even though it is
invisible end to end. If it is near zero, the latency argument is dead by measurement and
any driver work has to be justified by what it can *enforce* — early-abort streaming,
transaction-level bounds, per-query accounting — rather than by speed.

Bolt reports two server-side numbers per query, which makes the split measurable rather
than inferred:

    result_available_after   server time until the first record was ready
    result_consumed_after    server time spent streaming the rest

Client wall-clock minus those two is everything else: request encoding, network, record
decoding, Python object construction, and session bookkeeping.

Queries are swept across three cost scales, because the answer is a ratio and a ratio needs
a denominator that varies. A cheap query is where driver cost should dominate if it ever
does; an expensive one is where it should vanish. Reporting only one scale would answer a
different question than the one asked.

Row count is swept separately from db hits for the same reason: decoding cost scales with
*records returned*, not with work done, so a query that does little and returns much is the
case that would favour a faster driver.

Usage:
    python scripts/finbench/driver_overhead.py --database finbenchsf1000real \
        --params outputs/finbench/sf1000-real/curated_parameters.json \
        --password "$NEO4J_PASSWORD"
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Each probe pairs a name with a shape and a note on why it is in the sweep. `rows` shapes
# return many records for little work, which is where decode cost would show up.
PROBES = [
    ("point_lookup",
     "MATCH (a:Account {acct_no:$n}) RETURN a.acct_no AS v",
     "one indexed seek, one row — the floor, where driver cost is most visible"),
    ("one_hop_count",
     "MATCH (a:Account {acct_no:$n})-[:TRANSFER]->(b:Account) RETURN count(DISTINCT b) AS v",
     "small expansion, one row"),
    ("two_hop_count",
     "MATCH (a:Account {acct_no:$n})-[:TRANSFER]->(:Account)-[:TRANSFER]->(c:Account) "
     "RETURN count(DISTINCT c) AS v",
     "large expansion, one row — work without records"),
    ("rows_1k",
     "MATCH (a:Account) RETURN a.acct_no AS v LIMIT 1000",
     "1,000 records for trivial work — decode-dominated"),
    ("rows_50k",
     "MATCH (a:Account) RETURN a.acct_no AS v LIMIT 50000",
     "50,000 records — the case that would most favour a faster driver"),
]


def measure(driver, database: str, cypher: str, params: Dict[str, Any],
            repeats: int, timeout_s: float) -> Optional[Dict[str, Any]]:
    from neo4j.exceptions import Neo4jError

    samples: List[Dict[str, float]] = []
    for _ in range(repeats):
        with driver.session(database=database) as session:
            tx = session.begin_transaction(timeout=timeout_s)
            try:
                t0 = time.perf_counter()
                result = tx.run(cypher, **params)
                rows = 0
                for _row in result:
                    rows += 1
                summary = result.consume()
                wall_ms = (time.perf_counter() - t0) * 1000.0
                tx.commit()
            except Neo4jError:
                tx.close()
                return None
        avail = float(summary.result_available_after or 0)
        consumed = float(summary.result_consumed_after or 0)
        samples.append({"wall_ms": wall_ms, "server_ms": avail + consumed,
                        "available_ms": avail, "consumed_ms": consumed,
                        "rows": float(rows)})

    def med(key: str) -> float:
        return statistics.median(s[key] for s in samples)

    wall, server = med("wall_ms"), med("server_ms")
    # Clamped at zero: server-side timers have millisecond granularity, so on a
    # sub-millisecond query the reported server time can exceed the measured wall time.
    # Reporting a negative overhead would be an artefact, not a finding.
    overhead = max(0.0, wall - server)
    return {
        "wall_ms": round(wall, 3),
        "server_ms": round(server, 3),
        "available_ms": round(med("available_ms"), 3),
        "consumed_ms": round(med("consumed_ms"), 3),
        "client_overhead_ms": round(overhead, 3),
        "overhead_share": round(overhead / wall, 4) if wall else None,
        "rows": int(med("rows")),
        "repeats": repeats,
        "timer_granularity_note": (
            "server timers are integer milliseconds; below ~2 ms the share is dominated by "
            "that granularity and should not be read as precise"
        ) if wall < 2.0 else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--params", type=Path,
                        help="curated_parameters.json, to pick anchors by cost band")
    parser.add_argument("--anchor", type=int, default=None,
                        help="override the anchor used by the anchored probes")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--query-timeout", type=float, default=45.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from neo4j import GraphDatabase

    anchor = args.anchor
    if anchor is None and args.params and args.params.exists():
        with args.params.open('r', encoding='utf-8') as f:
            curated = json.load(f)
        # The medium band: large enough that server work is real, small enough to finish.
        band = next((b for b in curated["bands"] if b["band"] == "medium"),
                    curated["bands"][0])
        anchor = band["anchors"][0]["account_id"]
    if anchor is None:
        raise SystemExit("provide --anchor or --params")

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    results = []
    try:
        # One warm pass, discarded: the first query on a session pays connection setup and
        # would be reported as driver overhead when it is a one-off.
        with driver.session(database=args.database) as s:
            s.run("RETURN 1").consume()
        for name, cypher, note in PROBES:
            m = measure(driver, args.database, cypher, {"n": anchor},
                        args.repeats, args.query_timeout)
            if m is None:
                print(f"[driver] {name:16s} timeout", flush=True)
                results.append({"probe": name, "note": note, "error": "timeout"})
                continue
            results.append({"probe": name, "note": note, "anchor": anchor, **m})
            print(f"[driver] {name:16s} wall={m['wall_ms']:>9.3f}ms "
                  f"server={m['server_ms']:>9.3f}ms "
                  f"client={m['client_overhead_ms']:>8.3f}ms "
                  f"({(m['overhead_share'] or 0):.1%}) rows={m['rows']:,}", flush=True)
    finally:
        driver.close()

    ok = [r for r in results if "overhead_share" in r and r["overhead_share"] is not None]
    worst = max(ok, key=lambda r: r["overhead_share"]) if ok else None

    lines = ["# Driver overhead as a share of query wall-clock", "",
             f"database `{args.database}` · anchor {anchor} · median of {args.repeats} "
             f"repeats after one discarded warm pass", "",
             "`server_ms` is bolt's own `result_available_after + result_consumed_after`. "
             "`client_ms` is wall-clock minus that: encoding, network, decoding, object "
             "construction, session bookkeeping.", "",
             "| probe | rows | wall | server | client | client share |",
             "|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['probe']} | — | — | — | — | ({r['error']}) |")
            continue
        lines.append(
            f"| {r['probe']} | {r['rows']:,} | {r['wall_ms']:.3f}ms | "
            f"{r['server_ms']:.3f}ms | {r['client_overhead_ms']:.3f}ms | "
            f"**{(r['overhead_share'] or 0):.1%}** |")
    if worst:
        lines += ["", f"Worst case in this sweep: **{worst['probe']}** at "
                      f"{worst['overhead_share']:.1%} client share "
                      f"({worst['client_overhead_ms']:.3f} ms of "
                      f"{worst['wall_ms']:.3f} ms).", "",
                  "Read against the end-to-end split: the graph engine is 0.3-0.9% of agent "
                  "latency, so the driver's share of *that* is the number below which "
                  "replacing the driver cannot help felt latency at all."]
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"schema_version": "seocho.finbench.driver-overhead.v1",
             "database": args.database, "anchor": anchor,
             "repeats": args.repeats, "probes": results}, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
