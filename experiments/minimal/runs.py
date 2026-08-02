#!/usr/bin/env python3
"""Read every run back, without needing this session or a running backend.

    python3 experiments/minimal/runs.py                 list runs
    python3 experiments/minimal/runs.py --stages        one row per stage
    python3 experiments/minimal/runs.py --csv out.csv   tidy table for charting
    python3 experiments/minimal/runs.py --diff A B      what decided the difference

Each run directory holds four durable files:

    decisive.json  the factors that determine the result, hashed to a fingerprint
    trace.jsonl    one record per stage with full input and output payloads
    spans.jsonl    the same structure as OpenTelemetry spans, with timings
    result.json    the run's summary

Two runs with the same fingerprint must produce the same numbers. --diff exists
to answer the only question that matters when they do not: which declared factor
moved.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "outputs/minimal"


def load_runs(root: Path) -> list[dict[str, Any]]:
    runs = []
    for directory in sorted(root.glob("*/"), reverse=True):
        result = directory / "result.json"
        if not result.is_file():
            continue
        try:
            summary = json.loads(result.read_text())
        except json.JSONDecodeError:
            continue
        decisive = {}
        if (directory / "decisive.json").is_file():
            decisive = json.loads((directory / "decisive.json").read_text())
        runs.append({"dir": directory, "summary": summary, "decisive": decisive})
    return runs


def stage_rows(run: dict[str, Any]) -> Iterator[dict[str, Any]]:
    trace = run["dir"] / "trace.jsonl"
    if not trace.is_file():
        return
    for line in trace.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        row = {
            "run": run["dir"].name,
            "fingerprint": record.get("fingerprint", ""),
            "stage": record.get("stage", ""),
            "status": record.get("status", ""),
            "seconds": record.get("seconds", ""),
        }
        # Flatten scalar outputs so the table is chartable as-is.
        for key, value in (record.get("output") or {}).items():
            if isinstance(value, (int, float, bool, str)) and not isinstance(value, bool):
                row[f"out.{key}"] = value
            elif isinstance(value, bool):
                row[f"out.{key}"] = int(value)
        for key, value in (record.get("input") or {}).items():
            if isinstance(value, (int, float, str)):
                row[f"in.{key}"] = value
        yield row


def flatten(prefix: str, value: Any, into: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}" if prefix else k, v, into)
    elif isinstance(value, list):
        into[prefix] = ",".join(str(v) for v in value)
    else:
        into[prefix] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--stages", action="store_true", help="one row per stage")
    parser.add_argument("--csv", type=Path, help="write a tidy table for charting")
    parser.add_argument("--diff", nargs=2, metavar=("RUN_A", "RUN_B"),
                        help="compare the decisive factors of two runs")
    args = parser.parse_args()

    runs = load_runs(args.root)
    if not runs:
        print(f"no runs under {args.root}")
        return 1

    if args.diff:
        found = {}
        for name in args.diff:
            match = [r for r in runs if name in r["dir"].name]
            if not match:
                raise SystemExit(f"no run matching {name!r}")
            found[name] = match[0]
        left, right = (found[n] for n in args.diff)
        a, b = {}, {}
        flatten("", left["decisive"], a)
        flatten("", right["decisive"], b)
        print(f"A {left['dir'].name}  fingerprint {left['summary'].get('fingerprint')}")
        print(f"B {right['dir'].name}  fingerprint {right['summary'].get('fingerprint')}")
        differing = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if not differing:
            print("\nno declared factor differs. If the results differ, something "
                  "outside the decisive config moved, and that is a defect.")
        else:
            print("\ndeclared factors that differ:")
            for key in differing:
                print(f"  {key}\n    A = {a.get(key)}\n    B = {b.get(key)}")
        return 0

    if args.stages or args.csv:
        rows = [row for run in runs for row in stage_rows(run)]
        if not rows:
            print("no stage records found")
            return 1
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        if args.csv:
            with args.csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            print(f"{args.csv}  {len(rows)} rows, {len(columns)} columns")
            return 0
        head = ["run", "stage", "seconds"]
        extra = [c for c in columns if c.startswith("out.")][:4]
        print(" | ".join(head + extra))
        for row in rows:
            print(" | ".join(str(row.get(c, ""))[:26] for c in head + extra))
        return 0

    print(f"{'run':34s} {'fingerprint':18s} {'stages':>6s} {'sec':>7s}  summary")
    for run in runs:
        s = run["summary"]
        extras = {k: v for k, v in s.items()
                  if k not in ("fingerprint", "stages", "seconds", "per_case")
                  and isinstance(v, (int, float, str))}
        print(f"{run['dir'].name:34s} {str(s.get('fingerprint','')):18s} "
              f"{len(s.get('stages', [])):6d} {s.get('seconds', 0):7.2f}  "
              f"{json.dumps(extras, default=str)[:60]}")
    print(f"\n{len(runs)} runs under {args.root}")
    print("charting: runs.py --csv table.csv   |   compare: runs.py --diff <A> <B>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
