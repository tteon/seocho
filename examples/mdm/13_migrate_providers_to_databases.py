#!/usr/bin/env python3
"""Copy hq-42k provider stores into separate databases on one DozerDB DBMS.

This is a zero-cost topology correction: existing provider graphs from
``config/providers.yaml`` (one physical DozerDB per MARA model) are copied into
``config/provider_databases.yaml`` (one DBMS, one database per model-provider).
The source stores remain read-only; target databases are wiped and rebuilt.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)

from lib import federation  # noqa: E402

BATCH = 500


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _copy_provider(src, dst) -> dict:
    from neo4j import GraphDatabase
    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry

    t0 = time.perf_counter()
    src_driver = GraphDatabase.driver(src.uri, auth=_auth())
    main_store = Neo4jGraphStore(dst.uri, *_auth())
    main_driver = GraphDatabase.driver(dst.uri, auth=_auth())
    try:
        db_registry.register(dst.database)
        main_store.ensure_database(dst.database, wait_online=True)

        with src_driver.session(database=src.database) as session:
            nodes = session.run(
                "MATCH (n) RETURN elementId(n) AS eid, labels(n) AS labels, "
                "properties(n) AS props"
            ).data()
            rels = session.run(
                "MATCH (a)-[r]->(b) RETURN elementId(a) AS src, elementId(b) AS tgt, "
                "type(r) AS type, properties(r) AS props"
            ).data()

        with main_driver.session(database=dst.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            for idx in range(0, len(nodes), BATCH):
                rows = [
                    {
                        "labels": row["labels"],
                        "props": {
                            **row["props"],
                            "origin_eid": row["eid"],
                            "origin_instance": src.uri,
                            "origin_db": src.database,
                        },
                    }
                    for row in nodes[idx : idx + BATCH]
                ]
                session.run(
                    "UNWIND $rows AS row "
                    "CALL apoc.create.node(row.labels, row.props) YIELD node "
                    "RETURN count(node)",
                    rows=rows,
                ).consume()
            session.run(
                "CREATE INDEX origin_eid_idx IF NOT EXISTS "
                "FOR (n:LegalEntity) ON (n.origin_eid)"
            ).consume()
            for idx in range(0, len(rels), BATCH):
                session.run(
                    "UNWIND $rows AS row "
                    "MATCH (a {origin_eid: row.src}), (b {origin_eid: row.tgt}) "
                    "CALL apoc.create.relationship(a, row.type, row.props, b) YIELD rel "
                    "RETURN count(rel)",
                    rows=rels[idx : idx + BATCH],
                ).consume()
            got_nodes = session.run("MATCH (n) RETURN count(n) AS count").data()[0]["count"]
            got_rels = session.run("MATCH ()-[r]->() RETURN count(r) AS count").data()[0]["count"]
    finally:
        src_driver.close()
        main_driver.close()
        main_store.close()

    ok = got_nodes == len(nodes) and got_rels == len(rels)
    return {
        "provider": src.dept,
        "source_uri": src.uri,
        "target_database": dst.database,
        "nodes_src": len(nodes),
        "nodes_dst": got_nodes,
        "rels_src": len(rels),
        "rels_dst": got_rels,
        "seconds": round(time.perf_counter() - t0, 2),
        "ok": ok,
    }


def main() -> int:
    src_instances = {
        inst.dept: inst
        for inst in federation.load_instances(MDM_ROOT / "config" / "providers.yaml")
    }
    dst_instances = federation.load_instances(MDM_ROOT / "config" / "provider_databases.yaml")
    results = []
    for dst in dst_instances:
        src = src_instances[dst.dept]
        rec = _copy_provider(src, dst)
        results.append(rec)
        mark = "OK" if rec["ok"] else "MISMATCH"
        print(
            f"  [{mark}] {rec['provider']}: "
            f"{rec['nodes_dst']}/{rec['nodes_src']} nodes, "
            f"{rec['rels_dst']}/{rec['rels_src']} rels -> {rec['target_database']} "
            f"({rec['seconds']}s)"
        )
    bad = [rec for rec in results if not rec["ok"]]
    if bad:
        print(f"!! {len(bad)} provider database copy mismatch(es); do not benchmark")
        return 1
    print("== provider databases ready on the single DozerDB DBMS ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
