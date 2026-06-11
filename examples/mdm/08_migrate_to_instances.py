#!/usr/bin/env python3
"""Migrate department graphs to their own physical instances — $0, no LLM.

Bolt-to-bolt copy: ``mdm<dept>`` DB on the main instance -> the default DB of
that department's dedicated DozerDB container (bronze tier). After this, each
department's data lives on its own DBMS with its own endpoint — true data
sovereignty, the precondition for the federation story.

Provenance: every copied node keeps its main-instance identity as
``origin_eid`` (the elementId it had in the consolidated experiment DB), and
gets ``origin_instance``/``origin_db`` stamps, so gold-tier records can point
back to the *physical* source of every fact.

§8 safety: node labels / relationship types are NEVER interpolated — creation
goes through ``apoc.create.node`` / ``apoc.create.relationship`` (APOC is on
the shards). Idempotent: each target shard is wiped and rebuilt.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

import yaml  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

BATCH = 500


def _auth():
    return (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))


def migrate_dept(main_driver, dept: str, spec: dict) -> dict:
    from neo4j import GraphDatabase

    src_db = spec["source_db"]
    t0 = time.perf_counter()

    with main_driver.session(database=src_db) as s:
        nodes = s.run(
            "MATCH (n) RETURN elementId(n) AS eid, labels(n) AS labels, "
            "properties(n) AS props").data()
        rels = s.run(
            "MATCH (a)-[r]->(b) RETURN elementId(a) AS src, elementId(b) AS tgt, "
            "type(r) AS type, properties(r) AS props").data()

    shard = GraphDatabase.driver(spec["uri"], auth=_auth())
    try:
        with shard.session(database=spec["database"]) as s:
            s.run("MATCH (n) DETACH DELETE n").consume()
            for i in range(0, len(nodes), BATCH):
                rows = [{
                    "labels": n["labels"],
                    "props": {**n["props"], "origin_eid": n["eid"],
                              "origin_db": src_db,
                              "origin_instance": os.environ["NEO4J_URI"]},
                } for n in nodes[i:i + BATCH]]
                # apoc.create.node: labels stay data, never Cypher text (§8).
                s.run(
                    "UNWIND $rows AS r "
                    "CALL apoc.create.node(r.labels, r.props) YIELD node "
                    "RETURN count(node)", rows=rows).consume()
            s.run("CREATE INDEX origin_eid_idx IF NOT EXISTS "
                  "FOR (n:LegalEntity) ON (n.origin_eid)").consume()
            for i in range(0, len(rels), BATCH):
                s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a {origin_eid: r.src}), (b {origin_eid: r.tgt}) "
                    "CALL apoc.create.relationship(a, r.type, r.props, b) YIELD rel "
                    "RETURN count(rel)", rows=rels[i:i + BATCH]).consume()
            got_n = s.run("MATCH (n) RETURN count(n) AS c").data()[0]["c"]
            got_r = s.run("MATCH ()-[r]->() RETURN count(r) AS c").data()[0]["c"]
    finally:
        shard.close()

    ok = got_n == len(nodes) and got_r == len(rels)
    rec = {
        "dept": dept, "uri": spec["uri"], "source_db": src_db,
        "nodes_src": len(nodes), "nodes_dst": got_n,
        "rels_src": len(rels), "rels_dst": got_r,
        "seconds": round(time.perf_counter() - t0, 2), "ok": ok,
    }
    mark = "OK" if ok else "MISMATCH"
    print(f"  [{mark}] {dept}: {got_n}/{len(nodes)} nodes, "
          f"{got_r}/{len(rels)} rels -> {spec['uri']} ({rec['seconds']}s)")
    return rec


def main() -> int:
    from neo4j import GraphDatabase

    spec = yaml.safe_load((MDM_ROOT / "config" / "instances.yaml").read_text())
    main_driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=_auth())
    try:
        results = [migrate_dept(main_driver, dept, inst)
                   for dept, inst in spec["instances"].items()]
    finally:
        main_driver.close()
    bad = [r for r in results if not r["ok"]]
    if bad:
        print(f"!! {len(bad)} shard(s) mismatched — do not proceed to federation")
        return 1
    print("== bronze migration complete: every department on its own instance ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
