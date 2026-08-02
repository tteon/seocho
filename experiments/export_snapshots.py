#!/usr/bin/env python3
"""Export the extracted graphs to files, so the analysis outlives the database.

Every table in the paper is computed from a live Neo4j instance that exists on
one machine. Clone the repository onto a new machine and not a single number can
be reproduced, because the graphs are not in it.

That gap cannot be closed by re-running the extraction either. The extraction
calls hosted models — DeepSeek-V3.1, gpt-oss-120b, MiniMax-M2.7 — which are not
versioned artifacts and can be withdrawn. Nobody, including us, can produce
those graphs a second time.

So the graphs themselves become the data. One JSONL file per (condition, model,
case) workspace holding every node and every relationship, with a manifest
recording counts and a checksum. The extraction stays irreproducible, which is
honest and unavoidable; the analysis becomes reproducible, which is the part
that matters for checking the paper.

    python3 experiments/export_snapshots.py --tag v2
    python3 experiments/export_snapshots.py --tag v2 --verify   check only

Restoring is the reverse and lives in the same file, so a reader has both halves
in one place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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

URI = "bolt://localhost:7687"
SNAPSHOTS = ROOT / "snapshots"
MODELS = ("deepseek", "gptoss", "minimax27")


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(arm: str, model: str, tag: str) -> str:
    return f"arm{tag}{arm.lower()}{model}" if tag else f"arm{arm.lower()}{model}"


def workspace_for(arm: str, model: str, case: str, tag: str) -> str:
    return (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
            else f"arm{arm.lower()}-{model}-{case}")


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()[:32]


def export_pair(driver, arm: str, model: str, tag: str,
                cases: list[str], out_dir: Path) -> dict[str, Any]:
    """One file per workspace. Nodes carry every property; edges carry endpoints.

    Properties are exported whole rather than filtered to the ones the analysis
    happens to read today, because a later question will want a property this
    one did not, and the database will not be there to ask.
    """
    database = database_for(arm, model, tag)
    written, nodes_total, edges_total = [], 0, 0
    with driver.session(database=database) as session:
        for case in cases:
            workspace = workspace_for(arm, model, case, tag)
            nodes = session.run(
                "MATCH (n {_workspace_id:$w}) "
                "RETURN elementId(n) AS eid, labels(n) AS labels, "
                "       properties(n) AS props", w=workspace).data()
            if not nodes:
                continue
            edges = session.run(
                "MATCH (a {_workspace_id:$w})-[r]->(b {_workspace_id:$w}) "
                "RETURN elementId(a) AS source, elementId(b) AS target, "
                "       type(r) AS type, properties(r) AS props",
                w=workspace).data()
            path = out_dir / f"{arm}_{model}_{case}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "workspace", "arm": arm,
                                     "model": model, "case": case,
                                     "workspace_id": workspace,
                                     "database": database,
                                     "nodes": len(nodes),
                                     "relationships": len(edges)},
                                    ensure_ascii=False) + "\n")
                for node in nodes:
                    fh.write(json.dumps({"kind": "node", **node},
                                        ensure_ascii=False, default=str) + "\n")
                for edge in edges:
                    fh.write(json.dumps({"kind": "edge", **edge},
                                        ensure_ascii=False, default=str) + "\n")
            written.append({"file": path.name, "case": case,
                            "nodes": len(nodes), "relationships": len(edges),
                            "sha256": digest(path)})
            nodes_total += len(nodes)
            edges_total += len(edges)
    return {"arm": arm, "model": model, "database": database,
            "files": written, "nodes": nodes_total,
            "relationships": edges_total}


def load_snapshot(path: Path) -> dict[str, Any]:
    """Read one workspace back. The other half of the contract."""
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


def verify(out_dir: Path, manifest: dict[str, Any]) -> list[str]:
    problems = []
    for pair in manifest["pairs"]:
        for entry in pair["files"]:
            path = out_dir / entry["file"]
            if not path.is_file():
                problems.append(f"missing {entry['file']}")
                continue
            if digest(path) != entry["sha256"]:
                problems.append(f"checksum changed: {entry['file']}")
                continue
            snapshot = load_snapshot(path)
            if len(snapshot["nodes"]) != entry["nodes"]:
                problems.append(f"node count differs: {entry['file']}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--arms", default="A,C,D,E")
    ap.add_argument("--verify", action="store_true",
                    help="check the files on disk against the manifest")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    partial_dir = (ROOT / "outputs/evaluation/mdm_fedcat" /
                   f"log2026-reextract-{args.tag or 'v1'}")
    cases = sorted({json.loads(p.read_text())["case_id"]
                    for p in partial_dir.glob("*.json")})
    if not cases:
        raise SystemExit(f"no partials under {partial_dir}")

    out_dir = SNAPSHOTS / (args.tag or "v1")
    manifest_path = out_dir / "manifest.json"

    if args.verify:
        if not manifest_path.is_file():
            raise SystemExit(f"no manifest at {manifest_path}")
        manifest = json.loads(manifest_path.read_text())
        problems = verify(out_dir, manifest)
        for problem in problems:
            print(f"  {problem}")
        print(f"\n{len(problems)} problems across "
              f"{sum(len(p['files']) for p in manifest['pairs'])} files")
        return 1 if problems else 0

    run = observe.Run(ROOT / "outputs/minimal", "export-snapshots", {"decisive": {
        "tag": args.tag, "arms": arms, "models": list(MODELS),
        "cases": cases, "seed": 42}})

    out_dir.mkdir(parents=True, exist_ok=True)
    driver = GraphDatabase.driver(URI, auth=auth())
    pairs: list[dict[str, Any]] = []
    try:
        with run.stage("export", arms=arms, cases=len(cases)) as out:
            jobs = [(arm, model) for arm in arms for model in MODELS]
            results = parallel.io_map(
                lambda job: export_pair(driver, job[0], job[1], args.tag,
                                        cases, out_dir), jobs)
            pairs = [r for r in results if r]
            out["pairs"] = len(pairs)
            out["files"] = sum(len(p["files"]) for p in pairs)
            out["nodes"] = sum(p["nodes"] for p in pairs)
            out["relationships"] = sum(p["relationships"] for p in pairs)
    finally:
        driver.close()

    manifest = {
        "contract": "log2026.graph_snapshots.v1",
        "question": ("Can the analysis be reproduced without the database that "
                     "produced these graphs?"),
        "method": ("every node and relationship of every workspace written to "
                   "JSONL, one file per condition, model and case, each with a "
                   "SHA-256; properties exported whole rather than filtered to "
                   "what today's analysis reads"),
        "claim_boundary": ("This makes the ANALYSIS reproducible, not the "
                           "extraction. The graphs were produced by hosted "
                           "models that are not versioned artifacts and can be "
                           "withdrawn; nobody, including us, can generate them "
                           "again. These files are the primary record."),
        "tag": args.tag, "arms": arms, "models": list(MODELS),
        "cases": cases,
        "totals": {"files": sum(len(p["files"]) for p in pairs),
                   "nodes": sum(p["nodes"] for p in pairs),
                   "relationships": sum(p["relationships"] for p in pairs)},
        "pairs": pairs,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2,
                                        ensure_ascii=False) + "\n")

    size = sum(p.stat().st_size for p in out_dir.glob("*.jsonl"))
    print()
    print(f"{manifest['totals']['files']} workspace files, "
          f"{manifest['totals']['nodes']:,} nodes, "
          f"{manifest['totals']['relationships']:,} relationships")
    print(f"{size / 1048576:.1f} MB under {out_dir.relative_to(ROOT)}")
    print(f"manifest {manifest_path.relative_to(ROOT)}")

    run.finish({"files": manifest["totals"]["files"],
                "nodes": manifest["totals"]["nodes"],
                "megabytes": round(size / 1048576, 1),
                "artifact": str(manifest_path.relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
