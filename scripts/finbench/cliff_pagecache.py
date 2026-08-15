#!/usr/bin/env python3
"""Where does a constant-dbHits plan stop being constant-latency? (infrastructure axis)

The scale curve established that a label-anchored plan holds **25 db hits at every
scale factor** while an unlabeled one grows linearly. But db hits are a *logical* unit:
25 hits served from page cache and 25 hits served from NVMe differ by orders of
magnitude in wall time. So "constant db hits" does not by itself prove "constant
latency" — it proves constant *work*, and the cost of that work depends on where the
pages live.

The honest way to find the inflection is to shrink the cache rather than grow the data:
a 2.7 GB store against a 512 MB cache exercises the same eviction behaviour as a 27 GB
store against a 5 GB cache, at no hardware cost. That is the whole design here — the
question "do we need a bigger instance?" is answered by making the current one smaller.

For each page-cache size this restarts DozerDB, warms the cache, and measures the tuned
and naive plans, recording db hits (which should not move) alongside cold and warm
latency (which should).

Requires docker access; it restarts the neo4j container between steps.

Usage:
    python scripts/finbench/cliff_pagecache.py --database finbenchsf1000 \
        --src outputs/finbench/sf1000 --sizes 6G,2G,512M,128M \
        --password "$NEO4J_PASSWORD" --out outputs/finbench/cliff_pagecache.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


verify = _load("finbench_verify", "verify_scenarios.py")
protocol = _load("finbench_bench_protocol", "bench_protocol.py")


def _restart_with_profile(profile: Dict[str, str], *, container: str,
                          compose: List[str], override_path: Path) -> None:
    """Recreate the container under a memory profile.

    Constraining only Neo4j's page cache is not enough to force disk I/O: with no
    container memory limit the host's own page cache holds the store files, so a
    Neo4j-level miss is served from RAM anyway. That is why the first sweep found no
    cliff — it varied the internal cache while every miss still landed in OS cache.

    A cgroup ``mem_limit`` caps the OS page cache too, so the store genuinely cannot
    fit and reads reach the device. Heap has to shrink alongside it or the JVM will
    not start inside the limit.

    Written as a compose override so the committed SUT config stays the documented
    one and the sweep is reproducible from the file it writes.
    """
    lines = ["services:", "  neo4j:"]
    if profile.get("mem"):
        lines.append(f"    mem_limit: {profile['mem']}")
    lines.append("    environment:")
    lines.append(f"      - NEO4J_server_memory_pagecache_size={profile['cache']}")
    if profile.get("heap"):
        lines.append(f"      - NEO4J_server_memory_heap_initial__size={profile['heap']}")
        lines.append(f"      - NEO4J_server_memory_heap_max__size={profile['heap']}")
    override_path.write_text("\n".join(lines) + "\n")
    cmd = ["docker", "compose"]
    for f in compose:
        cmd += ["-f", f]
    cmd += ["-f", str(override_path), "up", "-d", "--no-deps", "--force-recreate", "neo4j"]
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # Health, then database availability — a container that is up can still be
    # replaying and answer 'unavailable'.
    for _ in range(60):
        probe = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True, text=True)
        if probe.stdout.strip() == "healthy":
            break
        time.sleep(3)


def _await_database(uri: str, user: str, password: str, database: str) -> bool:
    from neo4j import GraphDatabase

    for _ in range(60):
        try:
            driver = GraphDatabase.driver(uri, auth=(user, password))
            try:
                with driver.session(database="system") as session:
                    status = {r["name"]: r["currentStatus"]
                              for r in session.run("SHOW DATABASES")}
                if status.get(database) == "online":
                    return True
            finally:
                driver.close()
        except Exception:
            pass
        time.sleep(3)
    return False


def measure_step(profile: Dict[str, str], *, src: Path, database: str, uri: str,
                 user: str, password: str, repeats: int, container: str) -> Dict[str, Any]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        applied = {}
        with driver.session(database="system") as session:
            applied = {r["name"]: r["value"] for r in session.run(
                "CALL dbms.listConfig() YIELD name, value RETURN name, value")
                if r["name"] == "server.memory.pagecache.size"}
        # Warm first: an unwarmed number measures I/O, and the point here is to see
        # what happens when warming *cannot* fit the working set.
        warm = protocol.warm_up(driver, database)
    finally:
        driver.close()

    report = verify.verify(src, uri, user, password, database,
                           repeats=repeats, warm=False, container=container)
    step: Dict[str, Any] = {
        "profile": dict(profile),
        "requested_pagecache": profile["cache"],
        "effective_pagecache": applied.get("server.memory.pagecache.size"),
        "warm_up": {"method": warm.get("method")},
        "store_bytes": report.get("environment", {}).get("store_bytes"),
        "scenarios": [],
    }
    for scenario in report["scenarios"]:
        step["scenarios"].append({
            "name": scenario["name"],
            "tuned": {k: scenario["tuned"][k]
                      for k in ("db_hits", "sargable", "cold_ms", "latency_ms",
                                "p99_ms", "cold_warm_ratio")},
            "naive": {k: scenario["naive"][k]
                      for k in ("db_hits", "sargable", "cold_ms", "latency_ms",
                                "p99_ms", "cold_warm_ratio")},
        })
    return step


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument(
        "--profiles",
        default="mem=none,heap=8G,cache=6G;mem=6g,heap=3G,cache=1G;"
                "mem=4g,heap=2G,cache=1G;mem=2g,heap=1G,cache=512M",
        help="semicolon-separated memory profiles; mem=none leaves the container unlimited")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--container", default="graphrag-neo4j")
    parser.add_argument("--compose", default="compose.yaml,docker/compose.finbench.yaml")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--override", type=Path,
                        default=Path("outputs/finbench/_cliff-override.yml"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    profiles: List[Dict[str, str]] = []
    for spec in args.profiles.split(";"):
        spec = spec.strip()
        if not spec:
            continue
        entry = dict(kv.split("=", 1) for kv in spec.split(",") if "=" in kv)
        if entry.get("mem", "").lower() in ("none", "", "unlimited"):
            entry.pop("mem", None)
        profiles.append(entry)
    compose = [c.strip() for c in args.compose.split(",") if c.strip()]
    args.override.parent.mkdir(parents=True, exist_ok=True)

    steps: List[Dict[str, Any]] = []
    for profile in profiles:
        label = f"mem={profile.get('mem','unlimited')} heap={profile.get('heap','-')} cache={profile['cache']}"
        print(f"[cliff] {label} — restarting", flush=True)
        _restart_with_profile(profile, container=args.container, compose=compose,
                              override_path=args.override)
        if not _await_database(args.uri, args.user, args.password, args.database):
            steps.append({"profile": dict(profile), "requested_pagecache": profile["cache"],
                          "error": "database_not_online"})
            continue
        print(f"[cliff] {label} — measuring", flush=True)
        steps.append(measure_step(profile, src=args.src, database=args.database,
                                  uri=args.uri, user=args.user, password=args.password,
                                  repeats=args.repeats, container=args.container))

    report = {"schema_version": "seocho.finbench.cliff-pagecache.v1",
              "database": args.database, "steps": steps}

    lines = ["# Page-cache cliff — does constant db hits mean constant latency?", "",
             f"database `{args.database}` · store "
             f"{steps[0].get('store_bytes', '?') if steps else '?'} bytes", "",
             "Shrinking the cache exercises the same eviction behaviour as growing the",
             "data, at no hardware cost. Db hits are logical and should not move; latency",
             "is physical and should.", "",
             "| container mem | heap | pagecache | scenario | tuned hits | tuned p50 | tuned p99 | tuned cold | naive p50 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for step in steps:
        prof = step.get("profile", {})
        mem = prof.get("mem", "unlimited")
        heap = prof.get("heap", "-")
        if step.get("error"):
            lines.append(f"| {mem} | {heap} | {step['requested_pagecache']} | — | — | — | — | — | (error: {step['error']}) |")
            continue
        cache = step["effective_pagecache"] or step["requested_pagecache"]
        for sc in step["scenarios"]:
            lines.append(
                f"| {mem} | {heap} | {cache} | {sc['name']} | "
                f"{sc['tuned']['db_hits']} | {sc['tuned']['latency_ms']:.1f}ms | "
                f"{sc['tuned']['p99_ms']:.1f}ms | {sc['tuned']['cold_ms']:.0f}ms | "
                f"{sc['naive']['latency_ms']:.0f}ms |")
    markdown = "\n".join(lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
