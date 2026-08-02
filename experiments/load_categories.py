#!/usr/bin/env python3
"""Load the extracted graphs into Neo4j, isolated by category, ready to query.

Indexing was a stage. What it established is that the schema handed to an
extractor does not make independently built graphs agree, and that what does
align them is where a figure came from. Part 2 asks what that changes at
answering time, and for that the graphs have to be queryable rather than
archived.

Two levels of isolation, because the questions need both:

    database per category    a cross-category question is only a real question
                             if the categories are actually apart. One database
                             each, which is what DozerDB's multi-database
                             support is for and why no second container is
                             involved
    workspace per view       within a category, `<tag>-<category>-<model>-<case>`
                             keeps each model's reading of each case separate.
                             The merge key already includes the workspace, so
                             two views cannot fuse even where they agree

The anchor layer travels with the nodes. A figure that could be located in its
source carries `_anchor_passage`, `_anchor_offset`, `_anchor_literal`,
`_anchor_exact`, `_anchor_scale_ratio` and the surrounding text — so a served
value can show where it came from, and a query can find two views that read one
printed number differently. That is the capability Part 2 compares against, and
nothing in the extraction path provides it.

Loading is idempotent per workspace: an existing workspace is cleared before it
is rewritten, so a re-run replaces rather than doubles.

    python3 experiments/load_categories.py --tag s1 --dry-run
    python3 experiments/load_categories.py --tag s1
    python3 experiments/load_categories.py --tag s1 --check
"""
from __future__ import annotations

import argparse
import importlib.util
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
OUT_ROOT = ROOT / "outputs/minimal"
_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DBNAME = re.compile(r"^[a-z][a-z0-9]{2,62}$")


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(tag: str, category: str) -> str:
    """A DozerDB database name: lowercase alphanumeric, validated before use."""
    slug = re.sub(r"[^a-z0-9]", "", category.lower())
    name = f"{re.sub(r'[^a-z0-9]', '', tag.lower())}cat{slug}"
    if not _DBNAME.match(name):
        raise SystemExit(f"unsafe database name derived: {name!r}")
    return name


def workspace_for(tag: str, category: str, model: str, case: str) -> str:
    return f"{tag}-{re.sub(r'[^a-z0-9]', '', category.lower())}-{model}-{case}"


def load_cases() -> dict[str, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


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


def read_anchors(directory: Path) -> dict[str, dict[str, Any]]:
    """Recovered provenance, keyed by the exported element id."""
    path = directory / "anchors.jsonl"
    if not path.is_file():
        return {}
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "anchor" and record.get("eid"):
            found[record["eid"]] = record
    return found


def plan(directory: Path, tag: str, cases: dict[str, Any],
         arms: list[str]) -> dict[str, list[tuple[Path, str, str, str]]]:
    """Which snapshot file goes into which category database."""
    grouped: dict[str, list[tuple[Path, str, str, str]]] = defaultdict(list)
    for path in sorted(directory.glob("*.jsonl")):
        if path.name in ("anchors.jsonl",):
            continue
        parts = path.stem.split("_")
        if len(parts) < 3 or parts[0] not in arms:
            continue
        arm, model, case = parts[0], parts[1], "_".join(parts[2:])
        category = cases.get(case, {}).get("category")
        if not category:
            continue
        grouped[category].append((path, arm, model, case))
    return grouped


def ensure_database(driver, name: str) -> bool:
    with driver.session(database="system") as session:
        present = {r["name"] for r in
                   session.run("SHOW DATABASES YIELD name RETURN name")}
        if name in present:
            return False
        # No WAIT: DozerDB rejects it as an unsupported administration command.
        session.run(f"CREATE DATABASE {name} IF NOT EXISTS").consume()
        return True


def load_category(driver, database: str, tag: str, category: str,
                  files: list[tuple[Path, str, str, str]],
                  anchors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    nodes_written = edges_written = anchored = 0
    skipped: set[str] = set()
    with driver.session(database=database) as session:
        for path, arm, model, case in files:
            workspace = workspace_for(tag, category, model, case)
            # Idempotent: a re-run replaces this workspace rather than doubling it.
            session.run("MATCH (n {_workspace_id:$w}) DETACH DELETE n",
                        w=workspace).consume()
            snapshot = read_workspace(path)
            identity: dict[str, str] = {}
            for node in snapshot["nodes"]:
                labels = [l for l in node["labels"] if _LABEL.match(l)]
                skipped |= {l for l in node["labels"] if not _LABEL.match(l)}
                if not labels:
                    continue
                props = dict(node["props"])
                props["_workspace_id"] = workspace
                props["_category"] = category
                props["_model"] = model
                props["_case"] = case
                props["_condition"] = arm
                anchor = anchors.get(node["eid"])
                if anchor:
                    props["_anchor_passage"] = anchor["passage"]
                    props["_anchor_offset"] = anchor["offset"]
                    props["_anchor_literal"] = anchor["literal"]
                    props["_anchor_exact"] = anchor["exact"]
                    props["_anchor_scale_ratio"] = anchor["scale_ratio"]
                    props["_anchor_window"] = anchor["window"]
                    anchored += 1
                result = session.run(
                    f"CREATE (n:{':'.join(labels)}) SET n += $props, "
                    "n._export_id = $eid RETURN elementId(n) AS eid",
                    props=props, eid=node["eid"]).single()
                identity[node["eid"]] = result["eid"]
                nodes_written += 1
            for edge in snapshot["edges"]:
                if not _LABEL.match(edge["type"]):
                    skipped.add(edge["type"])
                    continue
                source = identity.get(edge["source"])
                target = identity.get(edge["target"])
                if not source or not target:
                    continue
                session.run(
                    "MATCH (a) WHERE elementId(a) = $a "
                    "MATCH (b) WHERE elementId(b) = $b "
                    f"CREATE (a)-[r:{edge['type']}]->(b) SET r += $props, "
                    "r._workspace_id = $w",
                    a=source, b=target, props=edge["props"],
                    w=workspace).consume()
                edges_written += 1
    return {"database": database, "category": category,
            "workspaces": len(files), "nodes": nodes_written,
            "relationships": edges_written, "anchored_nodes": anchored,
            "labels_skipped_as_unsafe": sorted(skipped)}


def check(driver, tag: str, grouped: dict[str, list], cases) -> list[str]:
    problems = []
    for category, files in sorted(grouped.items()):
        database = database_for(tag, category)
        try:
            with driver.session(database=database) as session:
                total = session.run(
                    "MATCH (n) RETURN count(n) AS c").single()["c"]
                workspaces = session.run(
                    "MATCH (n) RETURN count(DISTINCT n._workspace_id) AS c"
                ).single()["c"]
                anchored = session.run(
                    "MATCH (n) WHERE n._anchor_offset IS NOT NULL "
                    "RETURN count(n) AS c").single()["c"]
                leaked = session.run(
                    "MATCH (n) WHERE n._category <> $c RETURN count(n) AS c",
                    c=category).single()["c"]
            if workspaces != len(files):
                problems.append(f"{database}: {workspaces} workspaces, "
                                f"{len(files)} expected")
            if leaked:
                problems.append(f"{database}: {leaked} nodes from another category")
            if total == 0:
                problems.append(f"{database}: empty")
        except Exception as exc:  # noqa: BLE001 — recorded, never imputed
            problems.append(f"{database}: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="s1")
    ap.add_argument("--arms", default="A")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    directory = SNAPSHOTS / args.tag
    if not (directory / "manifest.json").is_file():
        raise SystemExit(f"no snapshots under {directory}; export them first")
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]

    cases = load_cases()
    grouped = plan(directory, args.tag, cases, arms)
    anchors = read_anchors(directory)

    if args.dry_run:
        print(f"{args.tag}: {sum(len(v) for v in grouped.values())} workspaces "
              f"into {len(grouped)} category databases, "
              f"{len(anchors):,} anchors available")
        for category, files in sorted(grouped.items()):
            print(f"  {database_for(args.tag, category):24s} {category:20s} "
                  f"{len(files):4d} workspaces")
        return 0

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        if args.check:
            problems = check(driver, args.tag, grouped, cases)
            for problem in problems:
                print(f"  {problem}")
            print(f"\n{len(problems)} problems across {len(grouped)} databases")
            return 1 if problems else 0

        run = observe.Run(OUT_ROOT, "load-categories", {"decisive": {
            "tag": args.tag, "arms": arms,
            "isolation": "database per category, workspace per model and case",
            "anchors_attached": bool(anchors), "seed": 42}})

        with run.stage("databases", categories=len(grouped)) as out:
            created = []
            for category in sorted(grouped):
                if ensure_database(driver, database_for(args.tag, category)):
                    created.append(database_for(args.tag, category))
            out["created"] = created
            out["total"] = len(grouped)

        with run.stage("load", workspaces=sum(len(v) for v in grouped.values()),
                       anchors=len(anchors)) as out:
            results = parallel.io_map(
                lambda item: load_category(
                    driver, database_for(args.tag, item[0]), args.tag,
                    item[0], item[1], anchors),
                sorted(grouped.items()))
            loaded = [r for r in results if r]
            out["categories"] = len(loaded)
            out["nodes"] = sum(r["nodes"] for r in loaded)
            out["relationships"] = sum(r["relationships"] for r in loaded)
            out["anchored_nodes"] = sum(r["anchored_nodes"] for r in loaded)

        with run.stage("verify") as out:
            problems = check(driver, args.tag, grouped, cases)
            out["problems"] = problems
            out["clean"] = not problems
    finally:
        driver.close()

    payload = {
        "contract": "log2026.category_load.v1",
        "question": ("Are the extracted graphs loaded and queryable, isolated by "
                     "category, with provenance attached?"),
        "method": ("one database per category and one workspace per model and "
                   "case within it; the recovered anchor layer written onto each "
                   "node it covers; loading clears a workspace before rewriting "
                   "it so a re-run replaces rather than doubles"),
        "claim_boundary": ("Loading, not evaluation. Isolation is enforced by "
                           "the database and workspace boundaries and verified "
                           "by counting nodes attributed to another category; "
                           "it does not establish that the categories are "
                           "semantically separable, which section 1.0 examines "
                           "and does not settle."),
        "tag": args.tag,
        "categories": len(loaded),
        "nodes": sum(r["nodes"] for r in loaded),
        "relationships": sum(r["relationships"] for r in loaded),
        "anchored_nodes": sum(r["anchored_nodes"] for r in loaded),
        "anchor_coverage": (round(sum(r["anchored_nodes"] for r in loaded)
                                  / sum(r["nodes"] for r in loaded), 4)
                            if sum(r["nodes"] for r in loaded) else 0.0),
        "problems": problems,
        "by_category": loaded,
    }
    (run.dir / "category_load.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'database':26s} {'category':20s} {'ws':>4s} {'nodes':>7s} "
          f"{'edges':>7s} {'anchored':>9s}")
    for row in loaded:
        print(f"{row['database']:26s} {row['category']:20s} "
              f"{row['workspaces']:4d} {row['nodes']:7d} "
              f"{row['relationships']:7d} {row['anchored_nodes']:9d}")
    print(f"\n{payload['nodes']:,} nodes, {payload['relationships']:,} "
          f"relationships, {payload['anchored_nodes']:,} carrying provenance "
          f"({payload['anchor_coverage']:.1%})")
    print(f"{len(problems)} problems")
    for problem in problems:
        print(f"  {problem}")

    run.finish({"categories": len(loaded), "nodes": payload["nodes"],
                "anchor_coverage": payload["anchor_coverage"],
                "problems": len(problems),
                "artifact": str((run.dir / "category_load.json").relative_to(ROOT))})
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
