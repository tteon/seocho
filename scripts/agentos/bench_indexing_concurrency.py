"""Profile the indexing parallelism win (seocho-ia4, step 2).

Two measurements, deterministic (mock latency, no API/DB), to decide where effort
belongs:

1. **Extraction concurrency (I/O-bound):** per-chunk LLM extraction is a network
   round-trip. A mock extract fn sleeps a realistic latency; we sweep worker counts
   over N chunks and report wall-clock + speedup. This is the dominant indexing cost
   and the step-1 win — it is a CONCURRENCY problem (overlap waits), not a compute
   one, so a thread pool (concurrent_map) is the right tool; Rust buys nothing here.

2. **Interning throughput (CPU-bound):** the SharedInternTable is the compute-bound
   piece a Rust concurrent map could accelerate. We measure intern ops/sec so the
   step-3/step-4 decision (Rust or not) rests on data: if interning is far from the
   bottleneck vs the extraction wall-clock, Rust is NOT yet warranted.

Usage: python scripts/agentos/bench_indexing_concurrency.py --out outputs/agentos/indexing_concurrency.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

import sys
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.index.parallel import concurrent_map  # noqa: E402
from seocho.index.shared_intern import SharedInternTable  # noqa: E402

_N_CHUNKS = 12
_EXTRACT_LATENCY_S = 0.4     # a realistic per-chunk LLM extract round-trip
_INTERN_OPS = 200_000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    def mock_extract(_chunk):
        time.sleep(_EXTRACT_LATENCY_S)   # simulate the network round-trip
        return {"nodes": [], "relationships": []}

    # 1. extraction concurrency sweep
    conc: Dict[str, Any] = {}
    base = None
    for w in (1, 2, 4, 8, 12):
        t0 = time.perf_counter()
        concurrent_map(list(range(_N_CHUNKS)), mock_extract, max_workers=w)
        dt = time.perf_counter() - t0
        if base is None:
            base = dt
        conc[str(w)] = {"wall_s": round(dt, 3), "speedup": round(base / dt, 2)}

    # 2. interning throughput (CPU-bound) — is it anywhere near the bottleneck?
    t = SharedInternTable()
    t0 = time.perf_counter()
    for i in range(_INTERN_OPS):
        t.intern("ws", f"company|e{i % 5000}", f"id-{i % 5000}")
    intern_dt = time.perf_counter() - t0
    intern_ops_per_s = int(_INTERN_OPS / intern_dt)

    seq_extract_wall = conc["1"]["wall_s"]
    report = {
        "chunks": _N_CHUNKS,
        "extract_latency_s": _EXTRACT_LATENCY_S,
        "extraction_concurrency": conc,
        "interning": {
            "ops": _INTERN_OPS,
            "wall_s": round(intern_dt, 3),
            "ops_per_s": intern_ops_per_s,
            "time_to_intern_one_doc_of_extraction_worth": round(
                (_N_CHUNKS * 50) / intern_ops_per_s, 5),  # ~50 nodes/chunk
        },
        "verdict": {
            "extraction_is_io_bound_concurrency_win": conc["8"]["speedup"],
            "interning_share_of_a_sequential_extract": round(
                (intern_dt * (_N_CHUNKS * 50 / _INTERN_OPS)) / seq_extract_wall, 6),
            "rust_intern_table_warranted_now": intern_ops_per_s < 50_000,
        },
    }

    c = report["extraction_concurrency"]
    print("=== indexing parallelism profile (seocho-ia4 step 2) ===")
    print(f"  extraction ({_N_CHUNKS} chunks @ {_EXTRACT_LATENCY_S}s each, I/O-bound):")
    for w in ("1", "2", "4", "8", "12"):
        print(f"    workers={w:>2}: {c[w]['wall_s']:>6.2f}s  ({c[w]['speedup']}x)")
    print(f"  interning (CPU-bound): {intern_ops_per_s:,} ops/s "
          f"-> interning a whole doc's entities ~ "
          f"{report['interning']['time_to_intern_one_doc_of_extraction_worth']*1000:.2f} ms")
    print(f"  => extraction concurrency win (8w): {c['8']['speedup']}x; "
          f"Rust intern table warranted now: {report['verdict']['rust_intern_table_warranted_now']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
