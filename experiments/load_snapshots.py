#!/usr/bin/env python3
"""Load the committed graph snapshots into a fresh database.

The other half of export_snapshots.py. Without this the snapshots are an archive
nobody can compute on; with it, a clone plus a container reproduces every table
in the paper without a single model call.

Each workspace file is replayed into the database its header names, preserving
labels, every property, and the relationships between them. Node identity comes
from the exported element id, mapped to the new store's ids as it goes, so a
relationship lands between the same two nodes it did originally.

    uv run python experiments/load_snapshots.py --tag v2
    uv run python experiments/load_snapshots.py --tag v2 --check

`--check` compares what is in the database against the manifest without writing,
which is the thing to run after a load to confirm it landed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import parallel  # noqa: E402

URI = os.environ.get("SEOCHO_NEO4J_URI", "bolt://localhost:7687")
SNAPSHOTS = ROOT / "snapshots"
_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def read_workspace(path: Path) -> dict[str, Any]:
    header, nodes, edges = {}, [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        kind = record.pop("kind", "")
        if kind == "workspace":
            header = record
        elif kind == "node":
            nodes.append(record)
        elif kind == "edge":
            edges.append(record)
    return {"header": header, "nodes": nodes, "edges": edges}


def ensure_database(driver, name: str) -> None:
    with driver.session(database="system") as session:
        # No WAIT: DozerDB rejects it as an unsupported administration command.
        session.run(f"CREATE DATABASE {name} IF NOT EXISTS").consume()


def load_database(driver, database: str, files: list[Path]) -> dict[str, Any]:
    """Replay every workspace belonging to one database, in one session."""
    ensure_database(driver, database)
    nodes_written = edges_written = 0
    skipped_labels: set[str] = set()
    with driver.session(database=database) as session:
        for path in files:
            snapshot = read_workspace(path)
            workspace = snapshot["header"].get("workspace_id", "")
            session.run("MATCH (n {_workspace_id:$w}) DETACH DELETE n",
                        w=workspace).consume()
            identity: dict[str, str] = {}
            for node in snapshot["nodes"]:
                labels = [l for l in node["labels"] if _LABEL.match(l)]
                skipped_labels |= {l for l in node["labels"] if not _LABEL.match(l)}
                if not labels:
                    continue
                tag = ":".join(labels)
                # Keyed on the exported element id so a relationship can find
                # its endpoints again; the original key is preserved in props.
                result = session.run(
                    f"CREATE (n:{tag}) SET n += $props, n._export_id = $eid "
                    "RETURN elementId(n) AS eid",
                    props=node["props"], eid=node["eid"]).single()
                identity[node["eid"]] = result["eid"]
                nodes_written += 1
            for edge in snapshot["edges"]:
                if not _LABEL.match(edge["type"]):
                    skipped_labels.add(edge["type"])
                    continue
                source, target = identity.get(edge["source"]), identity.get(edge["target"])
                if not source or not target:
                    continue
                session.run(
                    "MATCH (a) WHERE elementId(a) = $a "
                    "MATCH (b) WHERE elementId(b) = $b "
                    f"CREATE (a)-[r:{edge['type']}]->(b) SET r += $props",
                    a=source, b=target, props=edge["props"]).consume()
                edges_written += 1
    return {"database": database, "workspaces": len(files),
            "nodes": nodes_written, "relationships": edges_written,
            "labels_skipped_as_unsafe": sorted(skipped_labels)}


def check(driver, manifest: dict[str, Any]) -> list[str]:
    problems = []
    for pair in manifest["pairs"]:
        database = pair["database"]
        try:
            with driver.session(database=database) as session:
                for entry in pair["files"]:
                    workspace = entry["file"].removesuffix(".jsonl")
                    parts = workspace.split("_")
                    arm, model, case = parts[0], parts[1], "_".join(parts[2:])
                    tag = manifest.get("tag", "")
                    wid = (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
                           else f"arm{arm.lower()}-{model}-{case}")
                    count = session.run(
                        "MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                        w=wid).single()["c"]
                    if int(count) != entry["nodes"]:
                        problems.append(
                            f"{entry['file']}: {count} nodes loaded, "
                            f"{entry['nodes']} expected")
        except Exception as exc:  # noqa: BLE001 — recorded, never imputed
            problems.append(f"{database}: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--check", action="store_true",
                    help="compare the database against the manifest, write nothing")
    args = ap.parse_args()

    from neo4j import GraphDatabase

    directory = SNAPSHOTS / (args.tag or "v1")
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text())

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        if args.check:
            problems = check(driver, manifest)
            for problem in problems:
                print(f"  {problem}")
            print(f"\n{len(problems)} problems across "
                  f"{manifest['totals']['files']} workspaces")
            return 1 if problems else 0

        by_database: dict[str, list[Path]] = defaultdict(list)
        for pair in manifest["pairs"]:
            for entry in pair["files"]:
                by_database[pair["database"]].append(directory / entry["file"])

        print(f"loading {manifest['totals']['files']} workspaces into "
              f"{len(by_database)} databases")
        results = parallel.io_map(
            lambda item: load_database(driver, item[0], item[1]),
            sorted(by_database.items()))
        loaded = [r for r in results if r]
        for row in loaded:
            print(f"  {row['database']:22s} {row['workspaces']:3d} workspaces, "
                  f"{row['nodes']:5d} nodes, {row['relationships']:5d} edges")
        unsafe = sorted({l for r in loaded for l in r["labels_skipped_as_unsafe"]})
        if unsafe:
            print(f"\nlabels skipped as unsafe to interpolate: {unsafe}")
        print(f"\n{sum(r['nodes'] for r in loaded):,} nodes, "
              f"{sum(r['relationships'] for r in loaded):,} relationships loaded")
        print("confirm with: --check")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
