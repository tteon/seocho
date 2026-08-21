#!/usr/bin/env python3
"""Sample GraphRAG-Bench evenly across hop depth, not uniformly.

Depth is the axis this benchmark uniquely supplies — Novel's `evidence_triple`
is the only gold structure in either corpus we have — and it is also the axis a
uniform sample destroys. 47.6% of Novel is single-hop, and on our own set every
arm scored full marks on single-hop questions. A uniform draw therefore spends
half its budget on items that cannot separate anything, which is the mechanical
reason this benchmark keeps producing ties.

Sampling is deterministic by content hash rather than by a seeded RNG. A seed
makes a run reproducible only if the seed is recorded and the input order never
changes; hashing the question id makes the same item selected by any run over
the same pool, including after the pool is re-annotated or reordered.

Items are emitted in the question-set shape the arms builder already consumes,
so nothing downstream needs to know this came from a benchmark:

  corpus       the `evidence` statements — the passages the vector arm reads
  gold_edges   the parsed `evidence_triple` — the same facts, as edges

That is the same knowledge-parity contract the synthetic set holds, and it is
what makes the comparison about form rather than access. It also means these
numbers measure ANSWERING, not retrieval, and are not comparable to published
GraphRAG-Bench leaderboards, which measure both together. Say so wherever they
appear.

Usage:
    scripts/serve_track/sample_bench_strata.py \\
        --annotated outputs/serve_track/bench_annotated.jsonl \\
        --per-bucket 8 --out outputs/serve_track/bench_sample.jsonl
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = 1

# 4+ is one bucket because the tail is thin: 5-hop and deeper together are 8.5%
# of Novel, so splitting them further buys resolution the counts cannot support.
BUCKETS = ("1", "2", "3", "4+")


def bucket_of(hops: Any) -> str | None:
    if not isinstance(hops, int) or hops < 1:
        return None
    return str(hops) if hops <= 3 else "4+"


def _rank(item: Dict[str, Any]) -> str:
    """Stable per-item key. Same pool in, same sample out, whatever the order."""
    return hashlib.sha256(str(item.get("id", "")).encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotated", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/bench_sample.jsonl"))
    parser.add_argument("--per-bucket", type=int, default=8)
    parser.add_argument("--min-corpus", type=int, default=1,
                        help="drop items with fewer supporting statements than this; "
                             "an empty vector arm is not a comparison")
    args = parser.parse_args()

    rows = [json.loads(line) for line in
            args.annotated.read_text(encoding="utf-8").splitlines() if line.strip()]

    pools: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    dropped_no_edges = dropped_no_corpus = 0
    for row in rows:
        edges = row.get("gold_edges") or []
        corpus = row.get("corpus") or []
        if not edges:
            dropped_no_edges += 1
            continue
        if len(corpus) < args.min_corpus:
            dropped_no_corpus += 1
            continue
        key = bucket_of((row.get("strata") or {}).get("hops"))
        if key:
            pools[key].append(row)

    sample: List[Dict[str, Any]] = []
    for key in BUCKETS:
        pool = sorted(pools.get(key, []), key=_rank)
        taken = pool[: args.per_bucket]
        for row in taken:
            strata = dict(row.get("strata") or {})
            strata["stratum"] = f"GB_hop_{key}"
            strata["hop_bucket"] = key
            sample.append({
                "schema_version": SCHEMA_VERSION,
                "id": row.get("id"),
                "question": row.get("question"),
                "answer": row.get("answer") or "",
                "excluded": [],
                "corpus": row.get("corpus") or [],
                "gold_edges": row.get("gold_edges") or [],
                "strata": strata,
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for item in sample:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"pool: {len(rows)} annotated, "
          f"{dropped_no_edges} without edges, {dropped_no_corpus} without statements")
    print(f"wrote {len(sample)} items to {args.out}\n")
    print(f"  {'bucket':10s}{'pool':>7s}{'taken':>7s}{'mean_stmts':>12s}{'mean_edges':>12s}")
    for key in BUCKETS:
        pool = pools.get(key, [])
        taken = [s for s in sample if s["strata"]["hop_bucket"] == key]
        if not taken:
            print(f"  {key:10s}{len(pool):7d}{0:7d}   (empty)")
            continue
        stmts = sum(len(s["corpus"]) for s in taken) / len(taken)
        edges = sum(len(s["gold_edges"]) for s in taken) / len(taken)
        print(f"  {key:10s}{len(pool):7d}{len(taken):7d}{stmts:12.1f}{edges:12.1f}")

    short = [key for key in BUCKETS if len(pools.get(key, [])) < args.per_bucket]
    if short:
        print(f"\nWARN: buckets with fewer items than requested: {short}. "
              f"The design is even coverage; an unfilled bucket silently "
              f"reweights the sample toward the shallow end.")


if __name__ == "__main__":
    main()
