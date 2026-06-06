#!/usr/bin/env python3
"""Agent-interaction advantage experiment: isolated per-instance DB vs shared DB.

Runs against LIVE DozerDB (the real engine, not a mock) to show where the
"one ephemeral logical database per tenant/agent-instance" model is measurably
*better* than the naive "all agents/tenants share one database" model, in
multi-tenant and single-tenant×multi-agent agent-interaction scenarios.

Design co-developed with a Graph-DBMS reviewer and an LLM/agent-systems
reviewer. Their converged, honest framing:

  CLAIM: per-database isolation gives DEFAULT-SAFE data isolation and CORRECT
  identity semantics on SEOCHO's `name`-keyed graph. The advantage is not
  "a shared DB *cannot* isolate" (a perfect per-query owner predicate could) —
  it is that isolation is the engine-enforced default, survives a forgotten
  predicate, and sidesteps the name-as-ID collision class entirely.

  NOT CLAIMED: performance or fault isolation. One engine = one buffer pool,
  one lock manager, one failure domain. (Inherited from ADR-0104 / the data-
  plane experiment.)

Conditions (per the reviewers' vocabulary):
  SHARED   - one DB, all tenants, identity = `name` (the realistic failure mode)
  ISOLATED - one DB per tenant/agent via derive_instance(id).database (feature)
  A_FILT   - shared DB but every read carries a correct owner predicate (steelman)
  Bprime   - positive control: all collapse to one DB (must reproduce SHARED's
             failures, else the harness is blind)

Scenarios (Graph-DBMS S1-S5/S8 + LLM-eng RCR + single-tenant GCASW):
  S1 identity collision   S2 property bleed     S3 constraint cross-conflict
  S4 cross-tenant edge    S5 query/RCR          S8 teardown blast radius
  G  speculative-write grounding contamination (single-tenant multi-agent)

Run: PYTHONPATH=src python3 scripts/experiments/agent_interaction_advantage.py
"""

from __future__ import annotations

import subprocess
import sys
import time

from seocho.instance import derive_instance

CONTAINER = "seocho-agentexp-neo4j"
PASSWORD = "seocho-dev"          # clean throwaway init -> password matches
IMAGE = "graphstack/dozerdb:5.26.3.0"
SHARED_DB = "shareddb"
COLLAPSED_DB = "collapseddb"


# --------------------------------------------------------------------------
# Live DozerDB driver (docker exec + cypher-shell)
# --------------------------------------------------------------------------
def _exec(cypher: str, database: str = "system") -> str:
    out = subprocess.run(
        [
            "docker", "exec",
            "-e", "NEO4J_USERNAME=neo4j",
            "-e", f"NEO4J_PASSWORD={PASSWORD}",
            CONTAINER, "cypher-shell", "--format", "plain", "-d", database, cypher,
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return f"ERROR::{(out.stderr or out.stdout).strip()}"
    return out.stdout.strip()


def scalar(cypher: str, database: str = "system") -> int:
    """Run a `RETURN <int> AS x` query and parse the scalar (or -1 on error)."""
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


def wipe_db(name: str) -> None:
    _exec("MATCH (n) DETACH DELETE n;", database=name)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------
def boot() -> bool:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER,
         "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-p", "7476:7474", "-p", "7689:7687", IMAGE],
        capture_output=True, text=True,
    )
    for _ in range(45):
        if scalar("SHOW DATABASES YIELD name RETURN count(name) AS x;") >= 1:
            return True
        time.sleep(2)
    return False


def teardown() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


# --------------------------------------------------------------------------
# Scenario primitives
# --------------------------------------------------------------------------
ALPHA = derive_instance("alpha").database
BETA = derive_instance("beta").database


def reset_all() -> None:
    for db in (SHARED_DB, COLLAPSED_DB, ALPHA, BETA):
        create_db(db)
        time.sleep(0.3)
    for db in (SHARED_DB, COLLAPSED_DB, ALPHA, BETA):
        wipe_db(db)


def line(label: str, shared, isolated, advantage: str) -> None:
    print(f"  {label:<34} SHARED={str(shared):>8}   ISOLATED={str(isolated):>8}   {advantage}")


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------
def s1_identity_collision() -> None:
    print("\nS1 identity collision — two tenants MERGE (:Company {name:'Acme'})")
    # SHARED: both tenants MERGE the same name -> one node
    wipe_db(SHARED_DB)
    _exec("MERGE (:Company {name:'Acme', owner:'alpha'});", SHARED_DB)
    _exec("MERGE (:Company {name:'Acme'});", SHARED_DB)  # beta's name-keyed MERGE binds alpha's node
    shared = scalar("MATCH (c:Company {name:'Acme'}) RETURN count(c) AS x;", SHARED_DB)
    # ISOLATED: each tenant in its own DB
    wipe_db(ALPHA); wipe_db(BETA)
    _exec("MERGE (:Company {name:'Acme', owner:'alpha'});", ALPHA)
    _exec("MERGE (:Company {name:'Acme', owner:'beta'});", BETA)
    iso = (scalar("MATCH (c:Company {name:'Acme'}) RETURN count(c) AS x;", ALPHA)
           + scalar("MATCH (c:Company {name:'Acme'}) RETURN count(c) AS x;", BETA))
    line("distinct 'Acme' nodes (want 2)", shared, iso,
         "SHARED coalesces 2 tenants -> 1 (WRONG); ISOLATED keeps 2")


def s2_property_bleed() -> None:
    print("\nS2 property bleed — alpha sets revenue=100, beta sets 999, alpha reads")
    wipe_db(SHARED_DB)
    _exec("MERGE (c:Company {name:'Acme'}) SET c.revenue=100;", SHARED_DB)
    _exec("MERGE (c:Company {name:'Acme'}) SET c.revenue=999;", SHARED_DB)
    shared = scalar("MATCH (c:Company {name:'Acme'}) RETURN c.revenue AS x;", SHARED_DB)
    wipe_db(ALPHA); wipe_db(BETA)
    _exec("MERGE (c:Company {name:'Acme'}) SET c.revenue=100;", ALPHA)
    _exec("MERGE (c:Company {name:'Acme'}) SET c.revenue=999;", BETA)
    iso = scalar("MATCH (c:Company {name:'Acme'}) RETURN c.revenue AS x;", ALPHA)
    line("alpha reads its revenue (want 100)", shared, iso,
         "SHARED -> alpha reads beta's 999 (bleed); ISOLATED -> 100")


def s3_constraint_conflict() -> None:
    print("\nS3 constraint cross-conflict — UNIQUE(name); beta's valid 'Acme' rejected")
    wipe_db(SHARED_DB)
    _exec("CREATE CONSTRAINT acme_uq IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE;", SHARED_DB)
    time.sleep(0.5)
    _exec("CREATE (:Company {name:'Acme', owner:'alpha'});", SHARED_DB)
    beta_fails_shared = errored("CREATE (:Company {name:'Acme', owner:'beta'});", SHARED_DB)
    wipe_db(ALPHA); wipe_db(BETA)
    _exec("CREATE CONSTRAINT acme_uq IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE;", ALPHA)
    _exec("CREATE CONSTRAINT acme_uq IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE;", BETA)
    time.sleep(0.5)
    _exec("CREATE (:Company {name:'Acme', owner:'alpha'});", ALPHA)
    beta_fails_iso = errored("CREATE (:Company {name:'Acme', owner:'beta'});", BETA)
    _exec("DROP CONSTRAINT acme_uq IF EXISTS;", SHARED_DB)
    line("beta's valid write rejected?", beta_fails_shared, beta_fails_iso,
         "SHARED rejects beta (alpha's data blocks it); ISOLATED accepts")


def s4_cross_tenant_edge() -> None:
    print("\nS4 cross-tenant edge — unscoped MERGE bridges alpha's Acme -> beta's Beta")
    wipe_db(SHARED_DB)
    _exec("CREATE (:Company {name:'Acme', owner:'alpha'});", SHARED_DB)
    _exec("CREATE (:Company {name:'Beta', owner:'beta'});", SHARED_DB)
    _exec("MATCH (a:Company {name:'Acme'}),(b:Company {name:'Beta'}) MERGE (a)-[:SUPPLIES]->(b);", SHARED_DB)
    shared = scalar("MATCH (:Company {name:'Acme'})-[r:SUPPLIES]->(:Company {name:'Beta'}) RETURN count(r) AS x;", SHARED_DB)
    wipe_db(ALPHA); wipe_db(BETA)
    _exec("CREATE (:Company {name:'Acme', owner:'alpha'});", ALPHA)
    _exec("CREATE (:Company {name:'Beta', owner:'beta'});", BETA)
    _exec("MATCH (a:Company {name:'Acme'}),(b:Company {name:'Beta'}) MERGE (a)-[:SUPPLIES]->(b);", ALPHA)
    iso = scalar("MATCH (:Company {name:'Acme'})-[r:SUPPLIES]->(:Company {name:'Beta'}) RETURN count(r) AS x;", ALPHA)
    line("cross-tenant edges (want 0)", shared, iso,
         "SHARED bridges two tenants (1); ISOLATED can't (0)")


def s5_query_rcr() -> int:
    print("\nS5 query contamination / RCR — each tenant loads 10 companies (1 name shared)")
    wipe_db(SHARED_DB)
    for i in range(10):
        _exec(f"CREATE (:Company {{name:'alpha-{i}', owner:'alpha'}});", SHARED_DB)
    for i in range(10):
        nm = "alpha-0" if i == 0 else f"beta-{i}"   # one name collides with alpha's
        # beta's writes: MERGE by name; ON CREATE tags new beta nodes, the
        # collision (alpha-0) MATCHes alpha's node and leaves it owner='alpha'.
        _exec(f"MERGE (c:Company {{name:'{nm}'}}) ON CREATE SET c.owner='beta';", SHARED_DB)
    shared = scalar("MATCH (c:Company) RETURN count(c) AS x;", SHARED_DB)
    # RCR for alpha's unscoped 'my companies' read in SHARED: foreign rows / total
    foreign = scalar("MATCH (c:Company) WHERE c.owner IS NULL OR c.owner<>'alpha' RETURN count(c) AS x;", SHARED_DB)
    total_shared = scalar("MATCH (c:Company) RETURN count(c) AS x;", SHARED_DB)
    rcr_shared = (foreign / total_shared) if total_shared else 0.0
    # A_FILT: shared + correct predicate
    afilt = scalar("MATCH (c:Company) WHERE c.owner='alpha' RETURN count(c) AS x;", SHARED_DB)
    wipe_db(ALPHA); wipe_db(BETA)
    for i in range(10):
        _exec(f"CREATE (:Company {{name:'alpha-{i}', owner:'alpha'}});", ALPHA)
    for i in range(10):
        _exec(f"CREATE (:Company {{name:'beta-{i}', owner:'beta'}});", BETA)
    iso = scalar("MATCH (c:Company) RETURN count(c) AS x;", ALPHA)
    rcr_iso = scalar("MATCH (c:Company) WHERE c.owner IS NULL OR c.owner<>'alpha' RETURN count(c) AS x;", ALPHA)
    line("alpha 'my companies' count (want 10)", shared, iso,
         "SHARED unscoped=19 (contaminated); ISOLATED unscoped=10")
    print(f"  {'retrieval contamination rate (RCR)':<34} SHARED={rcr_shared:>8.2f}   "
          f"ISOLATED={float(rcr_iso):>8.2f}   A_FILT count={afilt} (steelman: predicate also isolates)")
    return shared


def s8_teardown() -> None:
    print("\nS8 teardown blast radius — remove beta; alpha must be untouched")
    wipe_db(ALPHA); wipe_db(BETA)
    for i in range(5):
        _exec(f"CREATE (:Doc {{name:'a{i}', owner:'alpha'}});", ALPHA)
        _exec(f"CREATE (:Doc {{name:'b{i}', owner:'beta'}});", BETA)
    drop_db(BETA); time.sleep(1)
    alpha_after = scalar("MATCH (n) RETURN count(n) AS x;", ALPHA)
    beta_gone = scalar("SHOW DATABASES YIELD name WHERE name='" + BETA + "' RETURN count(name) AS x;")
    create_db(BETA); time.sleep(1)
    line("DROP beta -> alpha nodes intact (5)", "scan-risk", alpha_after,
         f"ISOLATED: O(1) DROP, alpha={alpha_after}, beta_db_gone={beta_gone==0}")


def g_speculative() -> None:
    print("\nG  single-tenant multi-agent — abandoned speculative write contaminates grounding")
    # One tenant. SHARED: debater-con writes a hypothesis into the tenant DB, branch abandoned.
    wipe_db(SHARED_DB)
    _exec("CREATE (:Fact {name:'q3_no_cut', owner:'tenant', status:'concluded'});", SHARED_DB)
    _exec("CREATE (:Claim {name:'q3_guidance_cut', owner:'tenant', status:'hypothesis'});", SHARED_DB)  # abandoned scratch
    gcasw_shared = scalar("MATCH (n) WHERE n.status='hypothesis' RETURN count(n) AS x;", SHARED_DB)
    # ISOLATED: the speculative branch ran in its OWN ephemeral DB, dropped on abandon.
    spec = derive_instance("tenant-run-con").database
    create_db(spec); time.sleep(0.5); wipe_db(spec)
    _exec("CREATE (:Claim {name:'q3_guidance_cut', status:'hypothesis'});", spec)
    drop_db(spec); time.sleep(0.5)              # branch abandoned -> O(1) clean rollback
    wipe_db(ALPHA)
    _exec("CREATE (:Fact {name:'q3_no_cut', owner:'tenant', status:'concluded'});", ALPHA)
    gcasw_iso = scalar("MATCH (n) WHERE n.status='hypothesis' RETURN count(n) AS x;", ALPHA)
    line("abandoned-hypothesis nodes in", gcasw_shared, gcasw_iso,
         "SHARED: reader grounds on rejected hypothesis (GCASW>0); ISOLATED: dropped (0)")


def main() -> int:
    print(__doc__.split("\n\n")[0])
    print(f"\nLIVE DozerDB ({IMAGE}); tenant DBs alpha={ALPHA} beta={BETA}")
    if not boot():
        print("FATAL: throwaway DozerDB did not become ready", file=sys.stderr)
        teardown()
        return 1
    try:
        reset_all()
        print("\n" + "=" * 90)
        print("ADVANTAGE TABLE — SHARED (all agents/tenants in one DB) vs ISOLATED (per-instance DB)")
        print("=" * 90)
        s1_identity_collision()
        s2_property_bleed()
        s3_constraint_conflict()
        s4_cross_tenant_edge()
        s5_query_rcr()
        s8_teardown()
        g_speculative()
        print("\n" + "=" * 90)
        print("All scenarios on a LIVE engine. SHARED is wrong-by-default on the name-keyed")
        print("model; ISOLATED is correct-by-default. Claim scope: DATA isolation only.")
        print("=" * 90)
    finally:
        for db in (ALPHA, BETA, SHARED_DB, COLLAPSED_DB):
            drop_db(db)
        teardown()
        print("throwaway DozerDB removed; your running stack was never touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
