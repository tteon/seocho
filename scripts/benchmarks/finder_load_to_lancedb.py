#!/usr/bin/env python3
"""Embed FinDER phase-experiment evidence into LanceDB.

Reads the 9 cases used in the phase experiment (P0 × 3, P1A × 1, P1B × 1,
P1C × 2, P1D × 2), splits each row's ``references_joined`` into its original
evidence chunks, embeds with OpenAI ``text-embedding-3-small`` (1536-dim by
default), and persists to a LanceDB table tagged with phase / case_id / slice /
modules so vector queries can be sliced the same way the Neo4j graph is.

Env:
  OPENAI_API_KEY — required for embedding API calls
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.finder.lib import bench_common as bc  # noqa: E402

REF_SEPARATOR = "===EVIDENCE_BOUNDARY==="
SLICES_CSV = ROOT / ".seocho/datasets/finder/slices/all_slices.csv"
LANCEDB_DIR = ROOT / ".seocho/lancedb"
TABLE_NAME = "finder_phase_evidence"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536  # OpenAI text-embedding-3-small default


def build_records(workspace_prefix: str) -> list[dict]:
    """Materialize the evidence chunks with phase/case/workspace metadata.

    Uses ``bench_common.PHASES`` as the single source of truth (T1.1 / §6.1).
    Each row gets a ``workspace_id`` so it can be joined against Neo4j nodes
    that share the same id.
    """
    df = pd.read_csv(SLICES_CSV)
    by_id = {row["_id"]: row for _, row in df.iterrows()}

    records: list[dict] = []
    for phase in bc.PHASES:
        modules = "+".join(phase.treatment_modules)
        for case_spec in phase.cases:
            row = by_id.get(case_spec.case_id)
            if row is None:
                print(f"  ! case {case_spec.case_id} not in slices CSV — skipping")
                continue
            workspace_id = bc.workspace_id_for(
                phase.code, case_spec.case_id, "loaded", prefix=workspace_prefix
            )
            refs = [
                r.strip()
                for r in str(row["references_joined"]).split(REF_SEPARATOR)
                if r.strip()
            ]
            for idx, text in enumerate(refs):
                records.append(
                    {
                        "id": f"{phase.code}::{case_spec.case_id}::{idx}",
                        "phase": phase.code,
                        "case_id": case_spec.case_id,
                        "slice": row["slice"],
                        "category": row["category"],
                        "type": row["type"] if isinstance(row.get("type"), str) else "",
                        "ontology_modules": modules,
                        "workspace_id": workspace_id,  # §6.1 — cross-script join key
                        "ref_idx": idx,
                        "ref_count": len(refs),
                        "n_chars": len(text),
                        "query": row["query"],
                        "expected_answer": row["answer"],
                        "text": text,
                    }
                )
    return records


def embed_batch(texts: list[str], *, model: str = EMBED_MODEL) -> list[list[float]]:
    """Call OpenAI embeddings API; supports batching with single request."""
    from openai import OpenAI
    from examples.finder.lib import llm_io

    client = OpenAI()
    # Retry transient errors via llm_io.with_retry
    return llm_io.with_retry(
        lambda: [
            d.embedding for d in client.embeddings.create(model=model, input=texts).data
        ],
        max_attempts=3,
        label="embed",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build records & print stats; skip API calls and LanceDB write.",
    )
    parser.add_argument("--table-name", default=TABLE_NAME)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop the LanceDB table if it exists before writing.",
    )
    parser.add_argument(
        "--workspace-prefix",
        default=f"finder-load-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}",
    )
    args = parser.parse_args()

    bc.bootstrap(verbose=True)

    # preflight (OpenAI embed key required for this step)
    report = bc.preflight(
        strict=not args.dry_run,
        require_moonshot=False,
        require_openai_embed=not args.dry_run,
        require_neo4j=False,
        require_slices=True,
    )
    report.print_table()
    if not args.dry_run and not report.ok:
        raise SystemExit("preflight failed — fix env/connectivity before running")

    records = build_records(args.workspace_prefix)
    print(
        f"= {len(records)} evidence chunks across {len({r['case_id'] for r in records})} cases ="
    )
    per_phase: dict[str, int] = {}
    for r in records:
        per_phase[r["phase"]] = per_phase.get(r["phase"], 0) + 1
    print("per phase:")
    for p, c in sorted(per_phase.items()):
        print(f"  {p}: {c} chunks")
    total_chars = sum(r["n_chars"] for r in records)
    print(f"total chars: {total_chars:,}  avg/chunk: {total_chars / len(records):.0f}")

    if args.dry_run:
        print("\n(dry-run) sample record (truncated):")
        sample = dict(records[0])
        sample["text"] = sample["text"][:300] + "…"
        sample["expected_answer"] = sample["expected_answer"][:200] + "…"
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        return 0

    # Embed in batches
    print(f"\nembedding {len(records)} chunks with {EMBED_MODEL}…")
    started = time.perf_counter()
    for i in range(0, len(records), args.batch_size):
        chunk = records[i : i + args.batch_size]
        vectors = embed_batch([r["text"] for r in chunk], model=EMBED_MODEL)
        for r, v in zip(chunk, vectors):
            r["vector"] = v
        print(f"  batch {i // args.batch_size + 1}: {len(chunk)} chunks done")
    elapsed = round(time.perf_counter() - started, 2)
    print(f"embedding done in {elapsed}s")

    # LanceDB write
    import lancedb

    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))
    if args.overwrite and args.table_name in db.table_names():
        db.drop_table(args.table_name)
        print(f"  dropped existing table {args.table_name}")
    table = db.create_table(
        args.table_name, data=records, mode="overwrite" if args.overwrite else "create"
    )
    n = len(table)
    print(f"\nLanceDB table '{args.table_name}' written: {n} rows @ {LANCEDB_DIR}")

    # Quick verify: vector dim + sanity sample
    sample = table.head(2).to_pylist()
    if sample:
        v = sample[0].get("vector") or []
        print(f"  vector dim: {len(v)}")
        print(
            f"  sample id: {sample[0]['id']}, phase: {sample[0]['phase']}, n_chars: {sample[0]['n_chars']}"
        )

    # Demo similarity search
    if sample:
        query = "year over year revenue growth and margin trend"
        try:
            q_vec = embed_batch([query], model=EMBED_MODEL)[0]
            hits = table.search(q_vec).limit(3).to_list()
            print(f"\nDemo similarity for '{query}':")
            for h in hits:
                print(
                    f"  [{h['phase']}/{h['case_id']}][{h.get('_distance', 0):.3f}] {h['text'][:80]}…"
                )
        except Exception as e:
            print(f"  (demo search skipped: {type(e).__name__}: {e})")

    # Manifest (atomic)
    out_dir = ROOT / "outputs/lancedb_load"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        out_dir
        / f"finder_phase_evidence_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    bc.atomic_write_json(
        manifest_path,
        {
            "table_name": args.table_name,
            "lancedb_dir": str(LANCEDB_DIR.relative_to(ROOT)),
            "embed_model": EMBED_MODEL,
            "embed_dim": EMBED_DIM,
            "rows": n,
            "per_phase": per_phase,
            "workspace_prefix": args.workspace_prefix,
            "elapsed_s": elapsed,
        },
    )
    print(f"\nmanifest → {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
