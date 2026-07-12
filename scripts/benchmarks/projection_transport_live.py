#!/usr/bin/env python3
"""Live PostgreSQL -> DozerDB transport comparison for projection rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import psycopg
from neo4j import GraphDatabase

from seocho.projection_format import rows_to_table, table_to_arrow_file, write_parquet_artifact


def fetch_rows(dsn: str, workspace: str, limit: int) -> list[dict]:
    with psycopg.connect(dsn) as connection:
        records = connection.execute(
            """SELECT workspace_id, sequence, memory_id, event_type, occurred_at,
                      provenance_id, payload_hash AS idempotency_key,
                      schema_version, payload
               FROM agent_memory_revisions WHERE workspace_id=%s
               ORDER BY sequence LIMIT %s""",
            (workspace, limit),
        ).fetchall()
    keys = ("workspace_id", "sequence", "memory_id", "event_type", "occurred_at",
            "provenance_id", "idempotency_key", "schema_version", "payload")
    return [dict(zip(keys, row, strict=True)) for row in records]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--password", required=True)
    parser.add_argument("--artifact-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--graph-files-ready", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = fetch_rows(args.dsn, args.workspace, args.limit)
    table_started = time.perf_counter()
    table = rows_to_table(rows)
    table_ms = (time.perf_counter() - table_started) * 1000
    arrow_path = args.artifact_dir / "seocho-projection-live.arrow"
    parquet_path = args.artifact_dir / "seocho-projection-live.parquet"
    arrow_started = time.perf_counter()
    arrow_payload = table_to_arrow_file(table)
    arrow_path.write_bytes(arrow_payload)
    arrow_encode_ms = (time.perf_counter() - arrow_started) * 1000
    parquet_started = time.perf_counter()
    parquet_receipt = write_parquet_artifact(table, parquet_path)
    parquet_encode_ms = (time.perf_counter() - parquet_started) * 1000
    canonical_json = json.dumps(table.to_pylist(), sort_keys=True, separators=(",", ":"), default=str).encode()

    report = {
        "schema_version": "seocho.projection-transport-live.v1",
        "source": "live-postgresql",
        "workspace_hash": hashlib.sha256(args.workspace.encode()).hexdigest()[:16],
        "rows": len(rows),
        "sequence": {"min": int(table["sequence"][0].as_py()), "max": int(table["sequence"][-1].as_py())},
        "serialization": {
            "table_ms": round(table_ms, 3),
            "arrow_file_bytes": len(arrow_payload),
            "arrow_encode_ms": round(arrow_encode_ms, 3),
            "parquet_bytes": parquet_receipt.byte_count,
            "parquet_encode_ms": round(parquet_encode_ms, 3),
            "json_bytes": len(canonical_json),
        },
        "graph": {"executed": False},
    }
    if args.graph_files_ready:
        driver = GraphDatabase.driver(args.bolt_uri, auth=("neo4j", args.password))
        timings: dict[str, list[float]] = {"bolt_unwind": [], "apoc_arrow": [], "apoc_parquet": []}
        pyrows = table.to_pylist()
        queries = {
            "bolt_unwind": ("UNWIND $rows AS value CREATE (:ProjectionTransportProbe {sequence:value.sequence, payload_hash:value.payload_sha256})", {"rows": pyrows}),
            "apoc_arrow": ("CALL apoc.load.arrow('seocho-projection-live.arrow') YIELD value CREATE (:ProjectionTransportProbe {sequence:value.sequence, payload_hash:value.payload_sha256})", {}),
            "apoc_parquet": ("CALL apoc.load.parquet('seocho-projection-live.parquet') YIELD value CREATE (:ProjectionTransportProbe {sequence:value.sequence, payload_hash:value.payload_sha256})", {}),
        }
        with driver.session() as session:
            for mode, (query, params) in queries.items():
                for _ in range(3):
                    session.run("MATCH (n:ProjectionTransportProbe) DETACH DELETE n").consume()
                    started = time.perf_counter()
                    session.run(query, **params).consume()
                    timings[mode].append((time.perf_counter() - started) * 1000)
                count = session.run("MATCH (n:ProjectionTransportProbe) RETURN count(n) AS n").single()["n"]
                if count != len(rows):
                    raise RuntimeError(f"{mode} row parity failed: {count} != {len(rows)}")
            session.run("MATCH (n:ProjectionTransportProbe) DETACH DELETE n").consume()
        driver.close()
        report["graph"] = {
            "executed": True,
            "runs_per_mode": 3,
            "latency_ms": {mode: {"median": round(statistics.median(values), 3), "runs": [round(v, 3) for v in values]} for mode, values in timings.items()},
            "row_parity": True,
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
