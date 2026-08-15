#!/usr/bin/env python3
"""Does memory matter once the graph has hubs? (the scoped-out half of the infra axis)

Two sweeps in this experiment concluded that memory does not matter: shrinking Neo4j's
page cache from 6 GiB to 128 MiB (4.5% of the store) left warm p50 at 2.1-2.2 ms, and
capping container memory with a cgroup limit left it at 1.9-2.9 ms right down to the
point where the database stopped starting at all. Both conclusions came with a
mechanism: the query's working set was about 25 pages, the same 25 pages every
repetition, so they stayed resident no matter how small the cache was.

That mechanism has an obvious boundary, and both sweeps ran on the wrong side of it.
They used a graph built by uniform random attachment, max degree 31. A 25-page working
set is not a property of the engine or of the plan — it is a property of a graph with no
hubs. On a power-law graph the same 2-hop question can touch a neighbourhood four to five
orders of magnitude larger, and that is the first working set in this experiment with any
chance of exceeding a cache.

So this re-runs the memory profiles against the hub graph. The anchors come from
``curate_parameters.py`` rather than being picked by hand: cost varies non-monotonically
with anchor degree, so uncurated anchors would inject variance larger than the effect
being measured. Each band is a stated working-set size, and the question is whether any
of them develops a cliff that the degree-less graph did not.

Db hits should stay flat across profiles — the plan does not change when memory does. If
latency also stays flat, "memory is a floor, not a curve" survives contact with hubs. If
it does not, the ranked synthesis needs reordering.

Usage:
    python scripts/finbench/cliff_hub_memory.py --database finbenchsf1000hub \
        --params outputs/finbench/sf1000-hub/curated_parameters.json \
        --password "$NEO4J_PASSWORD" --out outputs/finbench/cliff_hub_memory.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cliff = _load("finbench_cliff_pagecache", "cliff_pagecache.py")
protocol = _load("finbench_bench_protocol", "bench_protocol.py")

# The aggregate shape, deliberately. A LIMIT-able question early-terminates and was
# already shown to be flat from degree 6 to 158,315, so it cannot reveal a memory effect;
# the aggregate is the one that actually walks the neighbourhood.
CYPHER = (
    "MATCH (a:Account {id: $id})-[:TRANSFER]->(:Account)-[:TRANSFER]->(c:Account) "
    "RETURN count(DISTINCT c) AS n"
)


def _db_hits(plan: Dict[str, Any]) -> int:
    return int(plan.get("dbHits", 0) or 0) + sum(
        _db_hits(c) for c in plan.get("children", []) or [])


def measure_band(driver, database: str, anchors: List[Dict[str, Any]],
                 repeats: int, timeout_s: float) -> Dict[str, Any]:
    from neo4j.exceptions import Neo4jError

    hits: List[int] = []
    cold: List[float] = []
    warm: List[float] = []
    timeouts = 0
    for anchor in anchors:
        params = {"id": f"Account:{anchor['account_id']}"}
        try:
            with driver.session(database=database) as session:
                tx = session.begin_transaction(timeout=timeout_s)
                result = tx.run("PROFILE " + CYPHER, **params)
                list(result)
                summary = result.consume()
                tx.commit()
                hits.append(_db_hits(summary.profile or {}))

                t0 = time.perf_counter()
                tx = session.begin_transaction(timeout=timeout_s)
                tx.run(CYPHER, **params).consume()
                tx.commit()
                cold.append((time.perf_counter() - t0) * 1000.0)

                for _ in range(repeats):
                    t0 = time.perf_counter()
                    tx = session.begin_transaction(timeout=timeout_s)
                    tx.run(CYPHER, **params).consume()
                    tx.commit()
                    warm.append((time.perf_counter() - t0) * 1000.0)
        except Neo4jError:
            timeouts += 1
    if not warm:
        return {"timeouts": timeouts, "measured": 0}
    warm.sort()
    return {
        "measured": len(hits),
        "timeouts": timeouts,
        "db_hits_mean": round(statistics.mean(hits), 1) if hits else None,
        # Flat db hits across profiles is the control: it says the plan did not change
        # when memory did, so any latency movement is physical rather than a replan.
        "db_hits_cv": (round(statistics.pstdev(hits) / statistics.mean(hits), 4)
                       if hits and statistics.mean(hits) else None),
        "cold_ms": round(statistics.median(cold), 2) if cold else None,
        "p50_ms": round(statistics.median(warm), 2),
        "p99_ms": round(warm[min(len(warm) - 1, int(len(warm) * 0.99))], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--params", type=Path, required=True,
                        help="curated_parameters.json from curate_parameters.py")
    parser.add_argument("--bands", default="small,medium,large,huge",
                        help="which curated bands to sweep")
    parser.add_argument(
        "--profiles",
        default="mem=none,heap=8G,cache=6G;mem=6g,heap=3G,cache=1G;"
                "mem=4g,heap=2G,cache=1G;mem=4g,heap=2G,cache=256M",
        help="semicolon-separated memory profiles; mem=none leaves the container unlimited")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--container", default="graphrag-neo4j")
    parser.add_argument("--compose", default="compose.yaml,docker/compose.finbench.yaml")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--query-timeout", type=float, default=30.0)
    parser.add_argument("--anchors-per-band", type=int, default=3)
    parser.add_argument("--override", type=Path,
                        default=Path("outputs/finbench/_cliff-hub-override.yml"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from neo4j import GraphDatabase

    curated = json.loads(args.params.read_text())
    wanted = [b.strip() for b in args.bands.split(",") if b.strip()]
    bands = [b for b in curated["bands"] if b["band"] in wanted]
    if not bands:
        raise SystemExit(f"none of {wanted} present in {args.params}")

    profiles: List[Dict[str, str]] = []
    for spec in args.profiles.split(";"):
        spec = spec.strip()
        if not spec:
            continue
        entry = dict(kv.split("=", 1) for kv in spec.split(",") if "=" in kv)
        if entry.get("mem", "").lower() in ("none", "", "unlimited"):
            entry.pop("mem", None)
        profiles.append(entry)
    args.override.parent.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, Any]] = []
    for profile in profiles:
        label = (f"mem={profile.get('mem', 'unlimited')} "
                 f"heap={profile.get('heap', '-')} cache={profile['cache']}")
        print(f"[hubmem] {label} — restarting", flush=True)
        cliff._restart_with_profile(
            profile, container=args.container,
            compose=[c.strip() for c in args.compose.split(",") if c.strip()],
            override_path=args.override)
        if not cliff._await_database(args.uri, args.user, args.password, args.database):
            steps.append({"profile": dict(profile), "error": "database_not_online"})
            print(f"[hubmem] {label} — NOT ONLINE", flush=True)
            continue

        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        try:
            warm = protocol.warm_up(driver, args.database)
            step: Dict[str, Any] = {"profile": dict(profile),
                                    "warm_up": (warm or {}).get("method"), "bands": []}
            for band in bands:
                anchors = band["anchors"][: args.anchors_per_band]
                measured = measure_band(driver, args.database, anchors,
                                        args.repeats, args.query_timeout)
                step["bands"].append({"band": band["band"],
                                      "target_l2": band["target_l2"], **measured})
                p50 = measured.get("p50_ms")
                print(f"[hubmem] {label} {band['band']:7s} "
                      f"L2={band['target_l2']:>12,.0f} "
                      f"hits={measured.get('db_hits_mean') or 0:>12,.0f} "
                      f"p50={'—' if p50 is None else format(p50, ',.1f') + 'ms':>11} "
                      f"timeouts={measured['timeouts']}", flush=True)
            steps.append(step)
        finally:
            driver.close()

    report = {"schema_version": "seocho.finbench.cliff-hub-memory.v1",
              "database": args.database, "cypher": CYPHER,
              "anchors_from": str(args.params), "steps": steps}

    lines = [
        "# Memory sweep on the hub graph, with curated anchors", "",
        f"database `{args.database}` · aggregate 2-hop (no early exit) · "
        f"{args.anchors_per_band} curated anchors per band", "",
        "The earlier sweeps found no cliff on a graph with max degree 31, where the "
        "working set was ~25 pages. These bands are stated working-set sizes on a "
        "power-law graph. Db hits should stay flat across profiles; if latency does too, "
        "\"memory is a floor, not a curve\" holds with hubs.", "",
        "| container mem | heap | cache | band | target L2 | db hits | cold | p50 | p99 | timeouts |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for step in steps:
        prof = step.get("profile", {})
        mem, heap = prof.get("mem", "unlimited"), prof.get("heap", "-")
        if step.get("error"):
            lines.append(f"| {mem} | {heap} | {prof.get('cache','-')} | — | — | — | — | "
                         f"— | — | (error: {step['error']}) |")
            continue
        for b in step["bands"]:
            def _f(v, s="ms"):
                return "—" if v is None else f"{v:,.1f}{s}"
            lines.append(
                f"| {mem} | {heap} | {prof['cache']} | {b['band']} | "
                f"{b['target_l2']:,.0f} | "
                f"{'—' if b.get('db_hits_mean') is None else format(b['db_hits_mean'], ',.0f')} | "
                f"{_f(b.get('cold_ms'))} | {_f(b.get('p50_ms'))} | {_f(b.get('p99_ms'))} | "
                f"{b['timeouts']} |")
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
