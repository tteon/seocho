#!/usr/bin/env python3
"""Build thread-level case pools for context-graph guardrail scaling.

The pool is intentionally LLM-free. It samples dataset threads, records slice
coverage, and emits thread-id lists that can be passed directly to
``run_e1.py --thread-ids``. BC3 and AMI can be scanned in parallel because the
outputs are independent.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BC3 = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
DEFAULT_AMI = ROOT / "examples/contextgraph/datasets/ami_slices.csv"


@dataclass(slots=True)
class ThreadStats:
    dataset: str
    thread_id: str
    row_count: int = 0
    slices: Counter[str] = field(default_factory=Counter)
    total_refs: int = 0
    max_query_words: int = 0
    evidence_chars: int = 0

    def update(self, row: dict[str, str]) -> None:
        self.row_count += 1
        self.slices[str(row.get("slice") or row.get("category") or "UNKNOWN")] += 1
        try:
            self.total_refs += int(row.get("n_refs") or 0)
        except ValueError:
            pass
        try:
            self.max_query_words = max(self.max_query_words, int(row.get("query_words") or 0))
        except ValueError:
            pass
        self.evidence_chars = max(self.evidence_chars, len(row.get("references_joined") or ""))

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["slices"] = dict(sorted(self.slices.items()))
        record["has_e1"] = bool(self.slices.get("E1_FACT"))
        record["has_e2"] = bool(self.slices.get("E2_DECISION_SUMMARY"))
        record["has_e3"] = bool(self.slices.get("E3_PROPOSALS"))
        record["has_e4"] = bool(self.slices.get("E4_POSITIONS"))
        return record


def _thread_id(row_id: str) -> str:
    return str(row_id).split("#", 1)[0]


def _scan_dataset(path: str, dataset: str, sample_size: int, seed: int) -> dict[str, Any]:
    stats: dict[str, ThreadStats] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tid = _thread_id(row.get("_id", ""))
            if not tid:
                continue
            stats.setdefault(tid, ThreadStats(dataset=dataset, thread_id=tid)).update(row)

    candidates = sorted(stats.values(), key=lambda item: item.thread_id)
    complete = [
        item for item in candidates
        if {"E1_FACT", "E2_DECISION_SUMMARY", "E3_PROPOSALS", "E4_POSITIONS"}.issubset(item.slices)
    ]
    source = complete or candidates
    rng = random.Random(seed)
    selected = list(source)
    rng.shuffle(selected)
    selected = sorted(selected[: max(0, sample_size)], key=lambda item: item.thread_id)

    return {
        "dataset": dataset,
        "path": path,
        "thread_count": len(candidates),
        "complete_thread_count": len(complete),
        "selected_count": len(selected),
        "selected": [item.to_record() for item in selected],
    }


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc3", default=str(DEFAULT_BC3))
    ap.add_argument("--ami", default=str(DEFAULT_AMI))
    ap.add_argument("--bc3-threads", type=int, default=30)
    ap.add_argument("--ami-threads", type=int, default=30)
    ap.add_argument("--seed", type=int, default=260614)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    jobs = [
        (args.bc3, "bc3", args.bc3_threads, args.seed),
        (args.ami, "ami", args.ami_threads, args.seed + 1),
    ]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(_scan_dataset, *job) for job in jobs if Path(job[0]).exists()]
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda item: item["dataset"])
    payload = {
        "seed": args.seed,
        "parallel_workers": max(1, int(args.workers)),
        "datasets": results,
    }
    (out_dir / "case_pool.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    all_records: list[dict[str, Any]] = []
    for result in results:
        dataset = str(result["dataset"])
        selected = result["selected"]
        thread_ids = [str(item["thread_id"]) for item in selected]
        _write_lines(out_dir / f"{dataset}_thread_ids.txt", thread_ids)
        all_records.extend(selected)

    with (out_dir / "case_pool.csv").open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "dataset",
            "thread_id",
            "row_count",
            "slices",
            "total_refs",
            "max_query_words",
            "evidence_chars",
            "has_e1",
            "has_e2",
            "has_e3",
            "has_e4",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in all_records:
            writer.writerow({**record, "slices": json.dumps(record["slices"], sort_keys=True)})

    print(json.dumps({
        "out": str(out_dir),
        "datasets": {
            item["dataset"]: {
                "thread_count": item["thread_count"],
                "complete_thread_count": item["complete_thread_count"],
                "selected_count": item["selected_count"],
            }
            for item in results
        },
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
