#!/usr/bin/env python3
"""Prepare the official GraphRAG-Bench question set for a SEOCHO experiment.

The upstream corpus and generated output are intentionally local-only. This
script does not download, redistribute, modify, or infer textbook-span
provenance. Its JSONL output is a content-free reference ledger; a runner must
read the local upstream snapshot directly at execution time.

Example:
    uv run python scripts/benchmarks/prepare_graphrag_bench.py \
      --question-dir .seocho/datasets/graphrag-bench/questions \
      --out .seocho/benchmarks/graphrag-bench/cases.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.graphrag_bench import load_question_directory, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--question-dir",
        type=Path,
        required=True,
        help="Upstream questions/ directory containing FB/MC/MS/OE/TF.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Content-free local reference-ledger JSONL",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional manifest path; defaults beside --out",
    )
    parser.add_argument(
        "--limit-per-type",
        type=int,
        default=None,
        help="Balanced smoke subset only; retain full source digests",
    )
    args = parser.parse_args()

    cases, manifest = load_question_directory(
        args.question_dir, limit_per_type=args.limit_per_type
    )
    count = write_jsonl(cases, args.out)
    manifest_path = args.manifest or args.out.with_suffix(".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"prepared {count} content-free case references: {args.out}")
    print(f"manifest: {manifest_path}")
    print(
        "next gate: attach corpus spans + Text2Cypher/gold-governance labels before making semantic-lift claims"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
