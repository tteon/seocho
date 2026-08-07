#!/usr/bin/env python3
"""Measurement protocol for the FinBench scale experiments.

Our first scale numbers were taken without warm-up, once per query, and reported
as p50/p95 only. Vendor and LDBC guidance says all three are wrong:

* Neo4j's operations manual: the page cache is **empty at startup** and warms on
  demand, so a cold measurement mostly measures I/O. Page cache should be sized
  to store + growth + 10%; on a dedicated host roughly half of RAM goes to Neo4j.
* Benchmarking practice: run each query repeatedly (single-shot numbers are
  noise) and report the **99th percentile**, not just the median.
* LDBC's audit process requires a **Full Disclosure Report** — enough
  configuration detail for a third party to reproduce the result.

So this module provides:

  warm_up()             page-cache warm-up (apoc.warmup.run, with a scan fallback)
  measure()             cold + N warm repetitions -> p50/p95/p99/min/max
  capture_environment() FDR-style snapshot: versions, memory config, host, store

One deliberate asymmetry: dbHits is reported alongside latency because it is a
*logical* cost and therefore immune to warm-up state. That is why the tuned-vs-naive
plan result (25 vs 6.6M hits) survives these methodology problems while the absolute
latencies do not — a property worth stating explicitly when publishing numbers.

LDBC's auditing guidelines permit precomputed auxiliary structures (indexes,
views) provided they are kept up to date on update. The degree/`_hub_tier`
materialization used here falls in that permitted category rather than being a
benchmark short-cut.
"""

from __future__ import annotations

import json
import platform
import subprocess
from typing import Any, Dict, List, Optional, Sequence


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(round((len(ordered) - 1) * p)), len(ordered) - 1)]


def warm_up(driver: Any, database: str, *, use_apoc: bool = True) -> Dict[str, Any]:
    """Load the store into the page cache before measuring.

    ``apoc.warmup.run`` is the documented approach; the id-scan fallback exists
    because APOC may be absent or restricted, and measuring without any warm-up
    at all would report I/O rather than query cost.
    """
    result: Dict[str, Any] = {"method": None, "detail": None}
    if use_apoc:
        try:
            with driver.session(database=database) as session:
                row = session.run("CALL apoc.warmup.run(true, true, true)").single()
            result.update(method="apoc.warmup.run", detail=dict(row) if row else None)
            return result
        except Exception as exc:
            result["apoc_error"] = f"{type(exc).__name__}: {exc}"[:200]

    with driver.session(database=database) as session:
        nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    result.update(method="scan_fallback", detail={"nodes": nodes, "relationships": rels})
    return result


def measure(
    session: Any,
    cypher: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    repeats: int = 10,
) -> Dict[str, Any]:
    """One cold run plus ``repeats`` warm runs.

    The cold/warm split is reported rather than averaged away: the gap between
    them is itself a scalability signal (how much of the answer came from disk).
    """
    import time

    params = params or {}

    start = time.perf_counter()
    rows = [r.data() for r in session.run(cypher, **params)]
    cold_ms = (time.perf_counter() - start) * 1000.0

    warm: List[float] = []
    for _ in range(max(0, repeats)):
        start = time.perf_counter()
        session.run(cypher, **params).consume()
        warm.append((time.perf_counter() - start) * 1000.0)

    return {
        "rows": len(rows),
        "cold_ms": cold_ms,
        "repeats": len(warm),
        "warm_ms": {
            "min": min(warm) if warm else None,
            "p50": _percentile(warm, 0.50),
            "p95": _percentile(warm, 0.95),
            "p99": _percentile(warm, 0.99),
            "max": max(warm) if warm else None,
        },
        # >1 means the cold run paid for I/O the warm runs did not.
        "cold_warm_ratio": (cold_ms / _percentile(warm, 0.50)) if warm and _percentile(warm, 0.50) else None,
        "result_sample": rows[:3],
    }


def _docker_exec(container: str, command: str) -> str:
    try:
        proc = subprocess.run(["docker", "exec", container, "sh", "-c", command],
                              capture_output=True, text=True, timeout=60)
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def capture_environment(driver: Any, database: str, *, container: str = "graphrag-neo4j") -> Dict[str, Any]:
    """FDR-style snapshot so a third party can reproduce the numbers."""
    env: Dict[str, Any] = {"schema_version": "seocho.finbench.fdr.v1", "database": database}

    with driver.session(database=database) as session:
        try:
            env["dbms"] = [r.data() for r in session.run(
                "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")]
        except Exception as exc:
            env["dbms_error"] = f"{type(exc).__name__}"
        try:
            env["memory_config"] = {
                r["name"]: r["value"] for r in session.run(
                    "CALL dbms.listConfig('server.memory') YIELD name, value RETURN name, value")
                if r["value"] is not None
            }
        except Exception:
            env["memory_config"] = {}
        try:
            env["graph"] = {
                "nodes": session.run("MATCH (n) RETURN count(n) AS c").single()["c"],
                "relationships": session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"],
            }
        except Exception:
            pass
        try:
            env["indexes"] = [
                {"name": r["name"], "type": r.get("type"), "state": r.get("state")}
                for r in session.run("SHOW INDEXES YIELD name, type, state RETURN name, type, state")
            ]
        except Exception:
            env["indexes"] = []

    # Effective page cache / heap as the running process sees it, plus host shape.
    conf = _docker_exec(container, "grep -E '^server.memory' /var/lib/neo4j/conf/neo4j.conf 2>/dev/null")
    env["conf_file_memory_lines"] = [line for line in conf.splitlines() if line.strip()]
    env["store_bytes"] = _docker_exec(
        container, f"du -sb /data/databases/{database} 2>/dev/null | cut -f1")
    env["container_mem_limit"] = _docker_exec(
        container, "cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null")
    env["host"] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": None,
        "mem_total_kb": None,
    }
    try:
        import os
        env["host"]["cpu_count"] = os.cpu_count()
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    env["host"]["mem_total_kb"] = int(line.split()[1])
                    break
    except Exception:
        pass
    return env


def recommend_page_cache(store_bytes: Optional[int]) -> Dict[str, Any]:
    """Neo4j's sizing rule: store + expected growth + 10%.

    Reported rather than applied, because the cliff experiment deliberately runs
    *under*-provisioned to find where a constant-dbHits plan stops being
    constant-latency.
    """
    if not store_bytes:
        return {"available": False}
    gib = store_bytes / (1024 ** 3)
    return {
        "available": True,
        "store_gib": round(gib, 2),
        "recommended_pagecache_gib": round(gib * 1.1 + 0.5, 2),
        "rule": "store size + expected growth + 10% (Neo4j operations manual)",
    }


if __name__ == "__main__":  # small self-check against a live database
    import argparse
    from neo4j import GraphDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="graphrag-neo4j")
    args = parser.parse_args()

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        env = capture_environment(driver, args.database, container=args.container)
        env["warm_up"] = warm_up(driver, args.database)
        store = int(env.get("store_bytes") or 0) or None
        env["page_cache_recommendation"] = recommend_page_cache(store)
        print(json.dumps(env, indent=2, default=str))
    finally:
        driver.close()
