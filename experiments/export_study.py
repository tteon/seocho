#!/usr/bin/env python3
"""Refresh the standalone study repository from this working tree.

The study repository was created by copying, and a copy with no way to refresh
it is a fork nobody declared. Within an hour the two had diverged — 48 experiment
files here against 41 there — and the divergence was silent, which is the part
that matters. A reproducibility repository that quietly lags the work it is
supposed to reproduce is worse than none, because it looks authoritative.

So there is one source, this tree, and one export. Nothing is edited on the
other side; running this brings it level and reports what moved.

    python3 experiments/export_study.py            what would change
    python3 experiments/export_study.py --apply    copy and stage it

What travels, and what does not
-------------------------------
Everything needed to re-run the analysis from a clone: the measurement scripts,
the findings, the manuscript, the snapshots that stand in for a database nobody
else has, the run records, and the pinned environment.

Not the secrets, not the FinDER parquet, which is public and fetched, not the
derived FIBO n-triples, which regenerate in seconds, and not any run record over
fifteen megabytes — those are named in the export report rather than dropped
quietly.

The commit measured is recorded in the export so the two repositories can always
be lined up, which is the thing the silent copy could not do.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path("/home/hadry/openup/LoG2026-ready")

# (source, destination). A directory copies recursively.
MOVES = [
    ("experiments", "experiments"),
    ("findings", "findings"),
    ("papers/log2026", "papers/log2026"),
    ("snapshots", "snapshots"),
    ("outputs/minimal", "outputs/minimal"),
    ("adjudication", "adjudication"),
    ("dataset/fibo/fibo-quickstart.ttl", "data/fibo-quickstart.ttl"),
    ("pyproject.toml", "pyproject.toml"),
    ("uv.lock", "uv.lock"),
]
SKIP_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIP_SUFFIX = {".pyc"}
MAX_BYTES = 15 * 1024 * 1024


def commit() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(args, cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    return {"commit": run("git", "rev-parse", "HEAD"),
            "short": run("git", "rev-parse", "--short", "HEAD"),
            "uncommitted_changes": bool(run("git", "status", "--porcelain"))}


def wanted(path: Path) -> bool:
    if any(part in SKIP_NAMES for part in path.parts):
        return False
    if path.suffix in SKIP_SUFFIX:
        return False
    return True


def plan() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    changes, oversized = [], []
    for source_rel, dest_rel in MOVES:
        source = ROOT / source_rel
        if not source.exists():
            continue
        if source.is_file():
            pairs = [(source, TARGET / dest_rel)]
        else:
            pairs = [(p, TARGET / dest_rel / p.relative_to(source))
                     for p in source.rglob("*") if p.is_file()]
        for src, dst in pairs:
            if not wanted(src):
                continue
            size = src.stat().st_size
            if size > MAX_BYTES:
                oversized.append({"path": str(src.relative_to(ROOT)),
                                  "megabytes": round(size / 1048576, 1)})
                continue
            if not dst.exists():
                changes.append({"path": str(dst.relative_to(TARGET)),
                                "action": "new", "src": str(src)})
            elif not filecmp.cmp(src, dst, shallow=False):
                changes.append({"path": str(dst.relative_to(TARGET)),
                                "action": "changed", "src": str(src)})
    return changes, oversized


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not TARGET.is_dir():
        raise SystemExit(f"no study repository at {TARGET}")

    changes, oversized = plan()
    version = commit()

    new = [c for c in changes if c["action"] == "new"]
    changed = [c for c in changes if c["action"] == "changed"]
    print(f"source at {version['short']}"
          + ("  (uncommitted changes present)"
             if version["uncommitted_changes"] else ""))
    print(f"{len(new)} new, {len(changed)} changed, "
          f"{len(oversized)} too large to travel\n")
    for entry in (new + changed)[:24]:
        print(f"  {entry['action']:8s} {entry['path']}")
    if len(changes) > 24:
        print(f"  … and {len(changes) - 24} more")
    if oversized:
        print("\nleft behind, over 15 MB:")
        for entry in oversized:
            print(f"  {entry['megabytes']:6.1f} MB  {entry['path']}")

    if not args.apply:
        print("\nnothing copied. Pass --apply.")
        return 0

    for entry in changes:
        destination = TARGET / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry["src"], destination)

    report = {
        "contract": "seocho.study_export.v1",
        "question": "Is the standalone study repository level with the work?",
        "claim_boundary": ("Copies files. It does not verify that the exported "
                           "analysis still runs, which is what the study "
                           "repository's own reproduction steps are for."),
        "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": version,
        "new": len(new), "changed": len(changed),
        "left_behind_oversized": oversized,
        "paths": [c["path"] for c in changes],
    }
    (TARGET / "EXPORT.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    subprocess.run(["git", "add", "-A"], cwd=TARGET, check=False,
                   capture_output=True)
    print(f"\ncopied {len(changes)} files and staged them in {TARGET}")
    print(f"source commit recorded in EXPORT.json: {version['short']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
