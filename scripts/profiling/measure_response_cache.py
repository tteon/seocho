#!/usr/bin/env python3
"""$0 measurement of the F2 persistent response cache effect (no LLM / no API).

Drives the SessionContext cache layer (the layer F2 changed) over a realistic
multi-session, repeated-query workload and counts LLM calls AVOIDED — the thing
that saves the user money. Three lanes:
  - no_cache : every query is an LLM call (baseline)
  - L1_only  : in-memory cache; only same-session repeats hit (dies per session)
  - L1+L2    : + persistent JSONL L2 → cross-session/process repeats also hit

Workload: U unique questions asked Q times with a Zipfian-ish repeat skew
(a few hot questions dominate — dashboards/FAQ), spread across S sessions (each
a fresh SessionContext = cold L1). graph_epoch held constant (no re-ingest); a
separate check shows a graph mutation (epoch bump) correctly invalidates.

$ projection uses the H2-measured avg prompt tokens per query (no live spend):
saved_tokens ≈ avoided_calls × avg_prompt_tokens.

Run: python scripts/profiling/measure_response_cache.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from seocho.agent.context import SessionContext
from seocho.response_cache import JSONLResponseCache
import tempfile, os

# H2-measured avg prompt tokens per query (gpt-4o, contextgraph) — for $ projection.
AVG_PROMPT_TOKENS = {"vector": 918, "graph": 1983, "hybrid": 2835}

U = 40        # unique questions
Q = 600       # total queries in the stream
S = 12        # sessions (each a fresh, cold-L1 SessionContext)
WS = "demo-workspace"
EPOCH = "100"  # graph node count, constant across the stream (no re-ingest)


def _zipf_stream(u: int, q: int):
    """Deterministic Zipfian-ish stream: question i drawn with weight 1/(i+1)."""
    weights = [1.0 / (i + 1) for i in range(u)]
    total = sum(weights)
    cum = []
    acc = 0.0
    for w in weights:
        acc += w / total
        cum.append(acc)
    # deterministic pseudo-draw (no RNG): walk a fixed low-discrepancy sequence
    stream = []
    x = 0.0
    for n in range(q):
        x = (x + 0.61803398875) % 1.0  # golden-ratio low-discrepancy
        idx = next(i for i, c in enumerate(cum) if x <= c)
        stream.append(f"question number {idx}?")
    return stream


def run_lane(stream, *, l2_path=None, per_session=True):
    """Return (llm_calls, hits). A 'hit' avoids an LLM call."""
    backend = JSONLResponseCache(l2_path) if l2_path else None
    sessions = [SessionContext(response_cache=backend) for _ in range(S)] if per_session else [
        SessionContext(response_cache=backend)]
    llm_calls = hits = 0
    for n, q in enumerate(stream):
        ctx = sessions[n % len(sessions)]
        ans = ctx.get_cached_answer(q, workspace_id=WS, ontology_identity_hash="h", graph_epoch=EPOCH)
        if ans is not None:
            hits += 1
            continue
        # miss → would call the LLM
        llm_calls += 1
        ctx.cache_query(q, f"answer to {q}", workspace_id=WS, ontology_identity_hash="h", graph_epoch=EPOCH)
    return llm_calls, hits


def main():
    stream = _zipf_stream(U, Q)
    print(f"== F2 response-cache effect ($0, no LLM) ==")
    print(f"workload: {U} unique Qs, {Q} total queries, {S} sessions, Zipfian repeat skew\n")

    # no cache: every query is a call
    no_cache_calls = len(stream)

    # L1 only: in-memory per-session (no L2)
    l1_calls, l1_hits = run_lane(stream, l2_path=None, per_session=True)

    # L1 + L2: persistent shared across sessions
    tmp = tempfile.mkdtemp(prefix="seocho_rc_")
    l2_path = os.path.join(tmp, "cache.jsonl")
    l2_calls, l2_hits = run_lane(stream, l2_path=l2_path, per_session=True)

    print(f"{'lane':<12}{'llm_calls':>10}{'avoided':>9}{'hit_rate':>10}")
    print("-" * 41)
    for name, calls in [("no_cache", no_cache_calls), ("L1_only", l1_calls), ("L1+L2", l2_calls)]:
        avoided = no_cache_calls - calls
        print(f"{name:<12}{calls:>10}{avoided:>9}{avoided/no_cache_calls:>9.0%}")

    print("\n-- projected token savings (H2 avg prompt tokens; no live spend) --")
    l2_avoided = no_cache_calls - l2_calls
    for lane, tok in AVG_PROMPT_TOKENS.items():
        print(f"  if served as {lane:<7} (~{tok} prompt tok/query): ~{l2_avoided*tok:,} prompt tokens saved")

    # correctness: a graph mutation (epoch bump) must NOT serve stale answers
    backend = JSONLResponseCache(l2_path)
    ctx = SessionContext(response_cache=backend)
    before = ctx.get_cached_answer("question number 0?", workspace_id=WS,
                                   ontology_identity_hash="h", graph_epoch=EPOCH)
    after = ctx.get_cached_answer("question number 0?", workspace_id=WS,
                                  ontology_identity_hash="h", graph_epoch="101")  # re-ingest
    print(f"\n-- invalidation check --")
    print(f"  same epoch hit:        {'YES' if before is not None else 'NO'}")
    print(f"  bumped epoch (stale):  {'served STALE (BUG)' if after is not None else 'miss (correct)'}")

    # cross-tenant: workspace B must not read A
    leak = ctx.get_cached_answer("question number 0?", workspace_id="other-tenant",
                                 ontology_identity_hash="h", graph_epoch=EPOCH)
    print(f"  cross-tenant leak:     {'LEAK (BUG)' if leak is not None else 'isolated (correct)'}")


if __name__ == "__main__":
    main()
