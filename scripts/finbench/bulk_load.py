#!/usr/bin/env python3
"""End-to-end bulk load of a FinBench snapshot into DozerDB via neo4j-admin.

Replaces the transactional loader for large scale factors. Measured on SF10
(33k nodes / 196k relationships):

    transactional (GraphStore.write over bolt)   874 s   ~142 rel/s
    neo4j-admin database import full             1.6 s   ~120,674 rel/s   (~849x)

That difference is what makes SF1000+ feasible at all — the transactional path
was bolt round-trip bound, so it was a software limit, not a database limit.

Flow: export CSV (DuckDB, streaming) -> copy into the container's import dir ->
drop the database -> offline import -> fix file ownership -> create/start ->
create the supporting indexes.

The ownership step is required: ``docker exec`` runs as root, so the imported
store files are root-owned and the neo4j process (uid 7474) cannot read them,
which surfaces as "Unable to start" with an AccessDeniedException.

Usage:
    python scripts/finbench/bulk_load.py --src outputs/finbench/sf100 \
        --database finbenchsf100 --password "$NEO4J_PASSWORD"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("finbench_export", _HERE / "export_import_csv.py")
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)  # type: ignore[union-attr]

_spec2 = importlib.util.spec_from_file_location("finbench_loader", _HERE / "load_to_graph.py")
loader = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(loader)  # type: ignore[union-attr]


def _run(cmd: list[str], timeout: int = 7200) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def bulk_load(*, src: Path, database: str, container: str, uri: str, user: str,
              password: str, staging: Path, keep_csv: bool = False) -> dict:
    from neo4j import GraphDatabase

    timings: dict[str, float] = {}
    csv_dir = staging / f"csv_{database}"
    if csv_dir.exists():
        shutil.rmtree(csv_dir)

    t0 = time.perf_counter()
    summary = exporter.export(src, csv_dir)
    timings["export_csv_s"] = time.perf_counter() - t0

    remote_dir = f"/var/lib/neo4j/import/csv_{database}"
    t0 = time.perf_counter()
    _run(["docker", "exec", "-u", "0", container, "sh", "-c", f"rm -rf {remote_dir} && mkdir -p {remote_dir}"])
    code, out = _run(["docker", "cp", f"{csv_dir}/.", f"{container}:{remote_dir}/"])
    if code != 0:
        raise RuntimeError(f"docker cp failed: {out[:400]}")
    timings["copy_s"] = time.perf_counter() - t0

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database="system") as session:
            session.run(f"DROP DATABASE {database} IF EXISTS").consume()

        t0 = time.perf_counter()
        code, out = _run(["docker", "exec", "-u", "0", container, "sh", "-c",
                          exporter.import_command(database, remote_dir)])
        timings["import_s"] = time.perf_counter() - t0
        if code != 0:
            raise RuntimeError(f"neo4j-admin import failed:\n{out[-1500:]}")
        imported = {
            key: int(line.strip().split()[0])
            for key in ("nodes", "relationships", "properties")
            for line in out.splitlines()
            if line.strip().endswith(key) and line.strip().split()[0].isdigit()
        }

        # docker exec runs as root; the server (uid 7474) must own the store.
        _run(["docker", "exec", "-u", "0", container, "sh", "-c",
              f"chown -R neo4j:neo4j /data/databases/{database} /data/transactions/{database} 2>/dev/null || true"])

        t0 = time.perf_counter()
        with driver.session(database="system") as session:
            session.run(f"CREATE DATABASE {database}").consume()
        status = ""
        for _ in range(120):
            with driver.session(database="system") as session:
                rows = {r["name"]: r["currentStatus"] for r in session.run("SHOW DATABASES")}
            status = rows.get(database, "")
            if status == "online":
                break
            if status == "offline":
                # A root-owned store shows up as offline; retry the start once.
                with driver.session(database="system") as session:
                    session.run(f"START DATABASE {database}").consume()
            time.sleep(1)
        timings["online_s"] = time.perf_counter() - t0
        if status != "online":
            raise RuntimeError(f"database {database} did not come online (status={status})")

        t0 = time.perf_counter()
        with driver.session(database=database) as session:
            for name, label, prop in loader.INDEX_SPECS:
                session.run(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})").consume()
            session.run("CALL db.awaitIndexes(600)").consume()
            counts = {
                "nodes": session.run("MATCH (n) RETURN count(n) AS c").single()["c"],
                "relationships": session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"],
            }
        timings["index_s"] = time.perf_counter() - t0
    finally:
        driver.close()
        if not keep_csv and csv_dir.exists():
            shutil.rmtree(csv_dir)

    rels = counts["relationships"]
    total = sum(timings.values())
    return {
        "schema_version": "seocho.finbench.bulk-load.v1",
        "src": str(src), "database": database,
        "csv_rows": summary["files"], "hub_threshold": summary["hub_threshold"],
        "imported": imported, "counts": counts,
        "timings_s": timings, "total_s": total,
        "relationships_per_second": rels / timings["import_s"] if timings.get("import_s") else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="graphrag-neo4j")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--staging", type=Path, default=Path("outputs/finbench/_staging"))
    parser.add_argument("--keep-csv", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    args.staging.mkdir(parents=True, exist_ok=True)
    report = bulk_load(src=args.src, database=args.database, container=args.container,
                       uri=args.uri, user=args.user, password=args.password,
                       staging=args.staging, keep_csv=args.keep_csv)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
