#!/usr/bin/env python3
"""Work out how every published result can be given a trace, and what it costs.

Seventy-two results are published and seven have a run directory behind them.
The other sixty-five are numbers with no record of how they were produced, which
is not an answer to a reviewer asking for the data.

Re-running everything is the wrong response: many of those scripts issue model
calls the user pays for, and some produce numbers that later work has already
replaced. So this classifies first.

For each contract it finds the script that writes it, reads that script to see
whether it calls a model or only reads what is already stored, and checks
whether a later contract supersedes it. The output is a plan with four buckets:

    traced      already has a run directory; nothing to do
    free        re-runnable now at no cost, reading stored data only
    paid        re-running means model calls; needs a decision, not a default
    retired     superseded by later work, or its inputs no longer exist

A result in `retired` is not swept away. It stays published with its supersession
recorded, because a withdrawn measurement that vanishes is how a reader loses
the ability to check what changed.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "experiments/results_index.json"

# Anything that reaches a paid endpoint. Grepped rather than imported, because
# importing forty scripts to find out would run their import-time side effects.
PAID_MARKERS = (
    "create_llm_backend", "llm.complete", "chat.completions", "OpenAI(",
    "run_under_opik_track", "client.add(", "Runner.run", "acomplete(",
)
# Reads stored state only. Present in a script with no paid marker means the
# rerun costs nothing but time.
FREE_MARKERS = (
    "GraphDatabase.driver", "SentenceTransformer", "json.loads", "read_text",
    "pd.read_parquet", "rdflib", "Graph()",
)


def load_index() -> list[dict[str, Any]]:
    if not INDEX.is_file():
        raise SystemExit("run experiments/registry.py --write first")
    payload = json.loads(INDEX.read_text())
    newest: dict[str, dict[str, Any]] = {}
    for entry in payload.get("results", []):
        current = newest.get(entry["contract"])
        if current is None or entry["modified"] > current["modified"]:
            newest[entry["contract"]] = entry
    return sorted(newest.values(), key=lambda e: e["contract"])


def tracked_files() -> set[str]:
    try:
        return set(subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.split())
    except Exception:  # noqa: BLE001
        return set()


_TRACKED = tracked_files()


def producing_script(contract: str) -> tuple[str, bool]:
    """The file that writes this contract, and whether it is committed.

    Plain grep rather than git grep: the scripts behind most of these results
    are not tracked, and searching only tracked files reported them as having no
    producer at all. Whether a script is committed is returned alongside,
    because an uncommitted producer is its own reproducibility problem — a
    reviewer cannot obtain the code that made the number.
    """
    try:
        found = subprocess.run(
            ["grep", "-rl", "--include=*.py", "--exclude-dir=.venv",
             "--exclude-dir=.git", "--", contract, "."],
            cwd=ROOT, capture_output=True, text=True).stdout.split()
    except FileNotFoundError:
        found = []
    scripts = [f.lstrip("./") for f in found if f.endswith(".py")]
    if not scripts:
        return "", False
    # Prefer the harness copy when a contract is written from two places.
    scripts.sort(key=lambda f: (not f.startswith("experiments/"), len(f)))
    return scripts[0], scripts[0] in _TRACKED


def classify(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    run_dir = ROOT / entry["run_dir"]
    row["traced"] = (run_dir / "trace.jsonl").is_file()
    script, committed = producing_script(entry["contract"])
    row["script"] = script
    row["script_committed"] = committed
    row["uses_harness"] = False
    row["paid_markers"] = []

    if script:
        path = ROOT / script
        try:
            source = path.read_text()
        except OSError:
            source = ""
        row["uses_harness"] = "import observe" in source
        row["paid_markers"] = sorted({m for m in PAID_MARKERS if m in source})
        row["reads_stored"] = any(m in source for m in FREE_MARKERS)
        try:
            ast.parse(source)
            row["parses"] = True
        except SyntaxError:
            row["parses"] = False
    else:
        row["reads_stored"] = False
        row["parses"] = False

    if row["traced"]:
        row["bucket"] = "traced"
    elif not script or not row["parses"]:
        row["bucket"] = "retired"
    elif row["paid_markers"]:
        row["bucket"] = "paid"
    else:
        row["bucket"] = "free"
    return row


def supersession(rows: list[dict[str, Any]]) -> dict[str, str]:
    """contract -> the newer version of the same contract, when one exists."""
    families: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        match = re.match(r"(.+)\.v(\d+)$", row["contract"])
        if match:
            families[match.group(1)].append((int(match.group(2)), row["contract"]))
    replaced = {}
    for family, versions in families.items():
        versions.sort()
        for _, contract in versions[:-1]:
            replaced[contract] = versions[-1][1]
    return replaced


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", help="print only one bucket, one path per line")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rows = [classify(entry) for entry in load_index()]
    replaced = supersession(rows)
    for row in rows:
        if row["contract"] in replaced and row["bucket"] != "traced":
            row["bucket"] = "retired"
            row["superseded_by"] = replaced[row["contract"]]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row["bucket"]].append(row)

    if args.bucket:
        for row in buckets.get(args.bucket, []):
            print(row["script"] or row["contract"])
        return 0

    for bucket in ("traced", "free", "paid", "retired"):
        group = buckets.get(bucket, [])
        print(f"\n[{bucket}]  {len(group)}")
        for row in group:
            note = ""
            if row.get("superseded_by"):
                note = f"  superseded by {row['superseded_by']}"
            elif row["paid_markers"]:
                note = f"  calls: {', '.join(row['paid_markers'][:3])}"
            elif not row["script"]:
                note = "  no script writes this contract"
            if row["script"] and not row.get("script_committed"):
                note += "  [script not committed]"
            print(f"  {row['contract']:44s} {row['script'][:44]:44s}{note}")

    print(f"\n{len(rows)} contracts: "
          + ", ".join(f"{len(v)} {k}" for k, v in sorted(buckets.items())))
    free_without_harness = [r for r in buckets.get("free", [])
                            if not r["uses_harness"]]
    uncommitted = [r for r in rows if r["script"] and not r["script_committed"]]
    print(f"{len(free_without_harness)} free reruns still need the harness wired in")
    print(f"{len(uncommitted)} results come from a script that is not committed, "
          f"so the code behind them cannot be handed to a reviewer either")

    if args.json:
        args.json.write_text(json.dumps({
            "contract": "seocho.retrace_plan.v1",
            "question": ("How can every published result be given a trace, and "
                         "what does each one cost?"),
            "claim_boundary": ("Classification is from static reading of the "
                               "script. A script with no paid marker could "
                               "still reach a paid service indirectly."),
            "counts": {k: len(v) for k, v in buckets.items()},
            "contracts": rows}, indent=2) + "\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
