#!/usr/bin/env python3
"""Run finder_judge.py over file chunks in parallel, then merge JSON outputs."""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmarks import finder_judge  # noqa: E402
from examples.finder.lib import bench_common as bc  # noqa: E402


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _prepare_chunk_dir(base: Path, index: int, files: list[str]) -> Path:
    chunk_dir = base / f"chunk_{index:04d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for pos, src in enumerate(files):
        target = chunk_dir / f"{pos:05d}_{Path(src).name}"
        if target.exists():
            continue
        os.symlink(Path(src).resolve(), target)
    return chunk_dir


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if key in row]
    return round(sum(values) / len(values), 3) if values else 0.0


def _merge(outputs: list[Path], out: Path, judge_models: list[str], evidence_mode: str) -> dict[str, Any]:
    judged: list[dict[str, Any]] = []
    for path in outputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        judged.extend(payload.get("results") or [])

    by: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in judged:
        by.setdefault(finder_judge.lane_key(row), []).append(row)
    summary: dict[str, Any] = {}
    for key, rows in sorted(by.items()):
        slc, retrieval, arm = key
        summary[f"{slc}|{retrieval}|{arm}"] = {
            "slice": slc,
            "retrieval": retrieval,
            "arm": arm,
            "n": len(rows),
            "judge_score_mean": _mean(rows, "panel_score"),
            "token_f1_mean": _mean(rows, "token_f1"),
            "overlap_mean": round(
                sum(float((row.get("evaluation") or {}).get("number_overlap_ratio") or 0) for row in rows)
                / len(rows),
                3,
            ),
            "correct": sum(1 for row in rows if row.get("panel_verdict") == "correct"),
            "partial": sum(1 for row in rows if row.get("panel_verdict") == "partial"),
            "incorrect": sum(1 for row in rows if row.get("panel_verdict") == "incorrect"),
        }
        evidence = [float(row["evidence_use_score"]) for row in rows if "evidence_use_score" in row]
        if evidence:
            summary[f"{slc}|{retrieval}|{arm}"]["evidence_use_score_mean"] = round(
                sum(evidence) / len(evidence), 3
            )
            summary[f"{slc}|{retrieval}|{arm}"]["evidence_judged"] = len(evidence)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_models": judge_models,
        "judge_prompt_id": finder_judge.JUDGE_PROMPT_ID,
        "evidence_judge_mode": evidence_mode,
        "n_judged": len(judged),
        "summary": summary,
        "inter_judge_agreement": (
            finder_judge._inter_judge_agreement(judged, judge_models)
            if len(judge_models) > 1 else {}
        ),
        "paired_vs_vector": finder_judge._paired_analysis(judged),
        "chunk_outputs": [str(path) for path in outputs],
        "results": judged,
    }
    bc.atomic_write_json(out, payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--judge-llms", default=finder_judge.JUDGE_MODEL)
    ap.add_argument("--judge-domain", default="financial", choices=sorted(finder_judge._JUDGE_SYSTEMS))
    ap.add_argument("--evidence-judge", default="auto", choices=("auto", "always", "never"))
    ap.add_argument("--evidence-judge-llms", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--chunk-size", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    files: list[str] = []
    for pattern in args.inputs:
        files.extend(sorted(glob.glob(pattern)))
    files = sorted(set(files))
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit("no input files matched")

    out = ROOT / args.out
    work_dir = Path(args.work_dir) if args.work_dir else out.with_suffix("").with_name(out.stem + "_chunks")
    work_dir.mkdir(parents=True, exist_ok=True)
    chunked = _chunks(files, max(1, int(args.chunk_size)))
    judge_models = [item.strip() for item in args.judge_llms.split(",") if item.strip()]

    def run_chunk(index: int, chunk_files: list[str]) -> Path:
        chunk_dir = _prepare_chunk_dir(work_dir, index, chunk_files)
        chunk_out = work_dir / f"chunk_{index:04d}.json"
        if args.resume and chunk_out.exists():
            return chunk_out
        cmd = [
            sys.executable,
            str(ROOT / "scripts/benchmarks/finder_judge.py"),
            "--judge-domain", args.judge_domain,
            "--judge-llms", args.judge_llms,
            "--evidence-judge", args.evidence_judge,
            "--inputs", str(chunk_dir / "*.json"),
            "--out", str(chunk_out.relative_to(ROOT) if chunk_out.is_relative_to(ROOT) else chunk_out),
        ]
        if args.evidence_judge_llms:
            cmd.extend(["--evidence-judge-llms", args.evidence_judge_llms])
        subprocess.run(cmd, cwd=str(ROOT), check=True)
        return chunk_out

    outputs: list[Path] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(run_chunk, idx, chunk) for idx, chunk in enumerate(chunked)]
        for fut in as_completed(futures):
            outputs.append(fut.result())
            print(f"completed chunk {len(outputs)}/{len(chunked)}", flush=True)
    outputs.sort()
    merged = _merge(outputs, out, judge_models, args.evidence_judge)
    print(json.dumps({
        "out": str(out),
        "n_judged": merged["n_judged"],
        "chunks": len(outputs),
        "paired_vs_vector": merged.get("paired_vs_vector", {}),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
