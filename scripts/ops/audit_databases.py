#!/usr/bin/env python3
"""Audit the DozerDB databases on this instance, and optionally drop dead ones.

The instance is one container hosting many logical databases, and it is now at
its cap, which blocks new experiments. Deciding what to remove needs evidence
rather than a guess from the name, because several names that look like scratch
("*smoke", "*probe") are cited by scripts and artifacts that are still read.

Evidence collected per database:

    nodes        how much is in it
    referenced   whether the name appears anywhere in the repository, meaning
                 a script, config, or committed artifact expects it to exist
    store_size   what it costs to keep

Classification:

    keep         referenced by the repository, or holds data and is named after
                 active work
    dead         zero nodes and unreferenced — nothing can be lost
    stale        holds data, unreferenced, and named as a probe or smoke test

Dropping is irreversible, so `--drop` takes an explicit class and prints what it
will remove before doing it. Nothing is dropped without that flag.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

URI = os.environ.get("SEOCHO_NEO4J_URI", "bolt://localhost:7687")
PROTECTED = {"system", "neo4j"}
SCRATCH_PATTERN = re.compile(r"(smoke|probe|sanity|test|tmp|scratch)", re.I)


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def repository_references(names: list[str]) -> dict[str, int]:
    """How many times each database name appears in tracked repository text.

    Uses git grep so untracked scratch output cannot make a database look alive.
    """
    counts = {name: 0 for name in names}
    try:
        listed = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return counts
    haystack = []
    for relative in listed:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 4_000_000:
            continue
        try:
            haystack.append(path.read_text(errors="ignore"))
        except OSError:
            continue
    blob = "\n".join(haystack)
    for name in names:
        counts[name] = blob.count(name)
    return counts


def collect() -> list[dict[str, Any]]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=auth())
    rows: list[dict[str, Any]] = []
    try:
        with driver.session(database="system") as session:
            listed = session.run(
                "SHOW DATABASES YIELD name, currentStatus RETURN name, currentStatus"
            ).data()
        names = sorted({r["name"] for r in listed})
        references = repository_references(names)
        for name in names:
            entry: dict[str, Any] = {"database": name,
                                     "referenced_in_repo": references.get(name, 0)}
            try:
                with driver.session(database=name) as session:
                    entry["nodes"] = int(session.run(
                        "MATCH (n) RETURN count(n) AS c").single()["c"])
                    entry["relationships"] = int(session.run(
                        "MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
            except Exception as exc:  # noqa: BLE001
                entry["nodes"] = -1
                entry["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(entry)
    finally:
        driver.close()
    return rows


def classify(entry: dict[str, Any]) -> str:
    name = entry["database"]
    if name in PROTECTED:
        return "protected"
    if entry.get("nodes", -1) < 0:
        return "unreadable"
    if entry["referenced_in_repo"] > 0:
        return "keep"
    if entry["nodes"] == 0:
        return "dead"
    if SCRATCH_PATTERN.search(name):
        return "stale"
    return "keep"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drop", choices=("dead", "stale"), action="append",
                    default=[], help="classes to drop; repeatable, irreversible")
    ap.add_argument("--json", type=Path, help="write the full audit")
    args = ap.parse_args()

    rows = collect()
    for entry in rows:
        entry["verdict"] = classify(entry)

    by_verdict: dict[str, list[dict[str, Any]]] = {}
    for entry in rows:
        by_verdict.setdefault(entry["verdict"], []).append(entry)

    print(f"{len(rows)} databases on {URI}\n")
    print(f"{'verdict':10s} {'database':34s} {'nodes':>10s} {'rels':>10s} {'refs':>5s}")
    for verdict in ("protected", "keep", "stale", "dead", "unreadable"):
        for entry in sorted(by_verdict.get(verdict, []),
                            key=lambda e: -e.get("nodes", 0)):
            print(f"{verdict:10s} {entry['database']:34s} {entry.get('nodes', 0):10,d} "
                  f"{entry.get('relationships', 0):10,d} "
                  f"{entry['referenced_in_repo']:5d}")

    counts = {v: len(rows_) for v, rows_ in sorted(by_verdict.items())}
    print(f"\n{counts}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"contract": "seocho.database_audit.v1", "uri": URI,
             "counts": counts, "databases": rows}, indent=2) + "\n")
        print(f"audit -> {args.json}")

    if not args.drop:
        print("\nnothing dropped. Pass --drop dead and/or --drop stale to remove.")
        return 0

    targets = [e["database"] for e in rows if e["verdict"] in args.drop]
    if not targets:
        print("\nno databases in the requested classes")
        return 0

    from neo4j import GraphDatabase

    print(f"\ndropping {len(targets)} databases: {', '.join(targets)}")
    driver = GraphDatabase.driver(URI, auth=auth())
    dropped, failed = [], []
    try:
        with driver.session(database="system") as session:
            for name in targets:
                try:
                    session.run(f"DROP DATABASE {name} IF EXISTS").consume()
                    dropped.append(name)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"database": name,
                                   "error": f"{type(exc).__name__}: {exc}"})
    finally:
        driver.close()
    print(f"dropped {len(dropped)}, failed {len(failed)}")
    for entry in failed:
        print(f"  {entry['database']}: {entry['error']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
