#!/usr/bin/env python3
"""MDM fan-out advantage: per-department isolated DB vs one shared DB.

Master Data Management framing of the worktree-isolated-runtime advantage
(seocho-6q9.3). Multiple departments' datasets are ingested by DIFFERENT agents
(fan-out). The question: load every department into ONE shared graph, or each
into its OWN logical database on the shared engine (the feature)?

Runs against LIVE DozerDB. The sharp wedge is the classic MDM problem:
cross-domain ENTITY COLLISION. Finance's "Apple" (a customer), Legal's "Apple"
(a contract counterparty), and HR's "Apple" (an employee) are three different
real-world things that share a name. On SEOCHO's `name`-keyed model a shared DB
silently fuses them; isolated per-department DBs keep them distinct — and the
MDM "golden record" is then built by a DELIBERATE, governed cross-DB merge
(staging-per-source -> match/merge), which is the MDM discipline itself.

Honest scope (per the GraphDBMS + LLM-engineer review): the advantage is
DEFAULT-SAFE isolation + correct entity identity. A shared DB with a perfect
`source_system` predicate on every clause could also isolate reads — but one
forgotten predicate fuses, and the name-as-ID collision is a *write*-time fusion
no read predicate fixes. Performance/fault isolation is NOT claimed (shared
engine = shared buffer pool + failure domain).

Run: PYTHONPATH=src python3 scripts/experiments/mdm_fanout_advantage.py
"""

from __future__ import annotations

import subprocess
import sys
import time

from seocho.instance import derive_instance

CONTAINER = "seocho-mdmexp-neo4j"
PASSWORD = "seocho-dev"
IMAGE = "graphstack/dozerdb:5.26.3.0"
SHARED_DB = "shareddb"

DEPARTMENTS = ["finance", "hr", "legal"]
# Each department is ingested by its own agent into its own derived DB (fan-out).
DEPT_DB = {d: derive_instance(d).database for d in DEPARTMENTS}


def _exec(cypher: str, database: str = "system") -> str:
    out = subprocess.run(
        ["docker", "exec", "-e", "NEO4J_USERNAME=neo4j", "-e", f"NEO4J_PASSWORD={PASSWORD}",
         CONTAINER, "cypher-shell", "--format", "plain", "-d", database, cypher],
        capture_output=True, text=True,
    )
    return f"ERROR::{(out.stderr or out.stdout).strip()}" if out.returncode else out.stdout.strip()


def scalar(cypher: str, database: str = "system") -> int:
    res = _exec(cypher, database)
    if res.startswith("ERROR::"):
        return -1
    lines = [ln for ln in res.splitlines() if ln.strip()]
    if len(lines) < 2:
        return 0
    try:
        return int(float(lines[-1].strip().strip('"')))
    except ValueError:
        return -1


def errored(cypher: str, database: str = "system") -> bool:
    return _exec(cypher, database).startswith("ERROR::")


def create_db(name: str) -> None:
    _exec(f"CREATE DATABASE `{name}` IF NOT EXISTS;")


def drop_db(name: str) -> None:
    _exec(f"DROP DATABASE `{name}` IF EXISTS;")


def wipe(name: str) -> None:
    _exec("MATCH (n) DETACH DELETE n;", name)


def boot() -> bool:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER,
         "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-p", "7478:7474", "-p", "7691:7687", IMAGE],
        capture_output=True, text=True,
    )
    for _ in range(45):
        if scalar("SHOW DATABASES YIELD name RETURN count(name) AS x;") >= 1:
            return True
        time.sleep(2)
    return False


# Department "datasets" each agent ingests. Note the deliberate name collision
# on 'Apple' across all three departments (three different real-world entities).
# Generic label 'Record' for every node: SEOCHO's memory graph is name-keyed,
# so without label discipline a name-keyed MERGE fuses cross-domain entities.
# This is the realistic MDM hazard (sources rarely agree on labels).
DATASET = {
    "finance": [("Record", "Apple", "revenue", 391),      # Apple Inc., a customer
                ("Record", "Beta Corp", "revenue", 12)],
    "hr":      [("Record", "Apple", "salary", 95),         # an employee literally named Apple
                ("Record", "Carol", "salary", 110)],
    "legal":   [("Record", "Apple", "contracts", 7),       # Apple as a contract counterparty
                ("Record", "Delta LLP", "contracts", 2)],
}


def ingest(db: str, dept: str) -> None:
    for label, name, prop, val in DATASET[dept]:
        _exec(f"MERGE (n:`{label}` {{name:'{name}'}}) SET n.{prop}={val}, n.dept='{dept}';", db)


def line(label, shared, isolated, note):
    print(f"  {label:<38} SHARED={str(shared):>9}   ISOLATED={str(isolated):>9}   {note}")


def m1_entity_coalescing():
    print("\nM1 cross-domain entity collision — 'Apple' in finance, hr, legal")
    wipe(SHARED_DB)
    for d in DEPARTMENTS:
        ingest(SHARED_DB, d)            # all agents fan-out into ONE shared DB
    shared = scalar("MATCH (n) WHERE n.name='Apple' RETURN count(n) AS x;", SHARED_DB)
    for d in DEPARTMENTS:
        wipe(DEPT_DB[d]); ingest(DEPT_DB[d], d)   # each agent into its OWN dept DB
    iso = sum(scalar("MATCH (n) WHERE n.name='Apple' RETURN count(n) AS x;", DEPT_DB[d]) for d in DEPARTMENTS)
    line("distinct 'Apple' entities (want 3)", shared, iso,
         "SHARED fuses 3 domains -> 1 node (MDM corruption); ISOLATED keeps 3")


def m2_attribute_corruption():
    print("\nM2 attribute corruption — how many depts' attributes pile on one 'Apple'?")
    # The fused SHARED 'Apple' accumulates revenue+salary+contracts from all 3
    # domains on one node, and n.dept is clobbered to the last writer.
    shared_props = scalar(
        "MATCH (n {name:'Apple'}) RETURN "
        "(CASE WHEN n.revenue IS NULL THEN 0 ELSE 1 END)+"
        "(CASE WHEN n.salary IS NULL THEN 0 ELSE 1 END)+"
        "(CASE WHEN n.contracts IS NULL THEN 0 ELSE 1 END) AS x;", SHARED_DB)
    iso_props = scalar(
        "MATCH (n {name:'Apple'}) RETURN "
        "(CASE WHEN n.revenue IS NULL THEN 0 ELSE 1 END)+"
        "(CASE WHEN n.salary IS NULL THEN 0 ELSE 1 END)+"
        "(CASE WHEN n.contracts IS NULL THEN 0 ELSE 1 END) AS x;", DEPT_DB["finance"])
    line("cross-domain attrs on 'Apple' (want 1)", shared_props, iso_props,
         "SHARED: 3 domains' attrs collide on 1 node; ISOLATED finance: only revenue")


def m4_cross_domain_edge():
    print("\nM4 cross-domain edge — accidental link finance.Apple -> legal.Delta LLP")
    _exec("MATCH (a {name:'Apple'}),(b {name:'Delta LLP'}) MERGE (a)-[:RELATED]->(b);", SHARED_DB)
    shared = scalar("MATCH ({name:'Apple'})-[r:RELATED]->({name:'Delta LLP'}) RETURN count(r) AS x;", SHARED_DB)
    # In isolated, finance DB has no 'Delta LLP' (that's legal's) -> pattern binds nothing
    _exec("MATCH (a {name:'Apple'}),(b {name:'Delta LLP'}) MERGE (a)-[:RELATED]->(b);", DEPT_DB["finance"])
    iso = scalar("MATCH ({name:'Apple'})-[r:RELATED]->({name:'Delta LLP'}) RETURN count(r) AS x;", DEPT_DB["finance"])
    line("cross-domain edges (want 0)", shared, iso,
         "SHARED bridges finance<->legal (>=1); ISOLATED can't (0)")


def m5_query_contamination():
    print("\nM5 query/RCR — finance agent does an unscoped 'MATCH (n)' over its memory")
    # contamination: rows visible to finance's unscoped read that aren't finance's
    foreign = scalar("MATCH (n) WHERE n.dept IS NULL OR n.dept<>'finance' RETURN count(n) AS x;", SHARED_DB)
    total = scalar("MATCH (n) RETURN count(n) AS x;", SHARED_DB)
    rcr = (foreign / total) if total else 0.0
    iso_total = scalar("MATCH (n) RETURN count(n) AS x;", DEPT_DB["finance"])
    iso_foreign = scalar("MATCH (n) WHERE n.dept<>'finance' RETURN count(n) AS x;", DEPT_DB["finance"])
    line("nodes in finance's unscoped view", total, iso_total,
         "SHARED scan crosses all domains; ISOLATED sees only finance")
    print(f"  {'retrieval contamination rate (RCR)':<38} SHARED={rcr:>9.2f}   "
          f"ISOLATED={(iso_foreign/iso_total if iso_total else 0):>9.2f}   foreign rows in finance's view")


def m6_selective_reload():
    print("\nM6 selective re-ingest — reload ONLY finance, others untouched")
    hr_before = scalar("MATCH (n) RETURN count(n) AS x;", DEPT_DB["hr"])
    drop_db(DEPT_DB["finance"]); time.sleep(1); create_db(DEPT_DB["finance"]); time.sleep(1)
    wipe(DEPT_DB["finance"]); ingest(DEPT_DB["finance"], "finance")
    hr_after = scalar("MATCH (n) RETURN count(n) AS x;", DEPT_DB["hr"])
    fin_after = scalar("MATCH (n) RETURN count(n) AS x;", DEPT_DB["finance"])
    line("reload finance -> HR untouched", "scan-delete risk", f"hr={hr_after}=={hr_before}",
         f"ISOLATED: DROP+recreate finance ({fin_after} nodes), HR intact ({hr_after})")


def m7_governed_golden_record():
    print("\nM7 GOLDEN RECORD — governed cross-DB merge (the MDM discipline)")
    # The CORRECT MDM workflow: staging isolated per source, then a DELIBERATE,
    # provenance-tracked match/merge into a master DB — not accidental name-fusion.
    master = derive_instance("mdm-master").database
    create_db(master); time.sleep(1); wipe(master)
    # deliberately reconcile the three 'Apple' records with explicit provenance
    for d in DEPARTMENTS:
        for label, name, prop, val in DATASET[d]:
            if name == "Apple":
                _exec(f"MERGE (m:MasterEntity {{name:'Apple'}}) "
                      f"MERGE (s:Source {{dept:'{d}', role:'{label}'}}) "
                      f"MERGE (m)-[:HAS_SOURCE]->(s) SET s.{prop}={val};", master)
    sources = scalar("MATCH (:MasterEntity {name:'Apple'})-[:HAS_SOURCE]->(s) RETURN count(s) AS x;", master)
    masters = scalar("MATCH (m:MasterEntity {name:'Apple'}) RETURN count(m) AS x;", master)
    drop_db(master)
    line("golden 'Apple': 1 master, 3 sources", "accidental fusion", f"{masters} master / {sources} src",
         "ISOLATED enables GOVERNED merge w/ provenance; SHARED already fused (no provenance)")


def main() -> int:
    print(__doc__.split("\n\n")[0])
    print(f"\nLIVE DozerDB; fan-out depts -> DBs: " + ", ".join(f"{d}={DEPT_DB[d]}" for d in DEPARTMENTS))
    if not boot():
        print("FATAL: throwaway DozerDB not ready", file=sys.stderr); subprocess.run(["docker", "rm", "-f", CONTAINER]); return 1
    try:
        create_db(SHARED_DB); time.sleep(0.5)
        for d in DEPARTMENTS:
            create_db(DEPT_DB[d]); time.sleep(0.4)
        print("\n" + "=" * 94)
        print("MDM FAN-OUT — SHARED single DB (all depts) vs ISOLATED per-department DB")
        print("=" * 94)
        m1_entity_coalescing()
        m2_attribute_corruption()
        m4_cross_domain_edge()
        m5_query_contamination()
        m6_selective_reload()
        m7_governed_golden_record()
        print("\n" + "=" * 94)
        print("LIVE engine. SHARED fan-out fuses cross-domain entities by name (MDM corruption);")
        print("ISOLATED fan-out = staging-per-source + governed golden-record merge. Data isolation only.")
        print("=" * 94)
    finally:
        for d in DEPARTMENTS:
            drop_db(DEPT_DB[d])
        drop_db(SHARED_DB)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        print("throwaway DozerDB removed; running stack untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
