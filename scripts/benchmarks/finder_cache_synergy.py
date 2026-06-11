#!/usr/bin/env python3
"""Synergy #1: persistent-cache hit-rate + latency win on repeated FinDER questions.

The council's synergy #1: the ontology-keyed persistent ResponseCache (seocho-jdg)
lets a FRESH session/process/worker reuse an answer computed earlier, so repeated
questions skip the expensive (DozerDB + LLM) compute. This measures it live:

  cold pass  (session1): compute + persist each answer
  warm pass  (session2, fresh in-memory cache): served from the PERSISTENT cache

Reports cross-session cache_hit_rate and cold-vs-warm p50/p99 latency.

Requires a reachable graph (DozerDB/Neo4j) and an LLM (MARA via MARA_API_KEY).
Run:  scripts/benchmarks/finder_cache_synergy.py --neo4j-password seocho-dev --limit 5
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src", ROOT / "extraction"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _percentile(values: List[float], p: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    k = min(len(vals) - 1, int(round((p / 100.0) * (len(vals) - 1))))
    return vals[k]


def summarize_cache_run(
    cold: List[Tuple[float, str]], warm: List[Tuple[float, str]]
) -> Dict[str, float]:
    """Pure aggregation (latency_ms, mode) rows -> synergy metrics. Unit-tested offline."""
    hits = sum(1 for _, mode in warm if mode == "cache_persistent")
    cold_p99 = _percentile([dt for dt, _ in cold], 99)
    warm_p99 = _percentile([dt for dt, _ in warm], 99)
    return {
        "n": float(len(warm)),
        "cache_hit_rate": (hits / len(warm)) if warm else 0.0,
        "cold_p50_ms": _percentile([dt for dt, _ in cold], 50),
        "cold_p99_ms": cold_p99,
        "warm_p50_ms": _percentile([dt for dt, _ in warm], 50),
        "warm_p99_ms": warm_p99,
        "warm_over_cold_p99_ratio": (warm_p99 / cold_p99) if cold_p99 else 0.0,
        "meets_0_5x_target": (cold_p99 > 0 and warm_p99 / cold_p99 < 0.5),
    }


def _mara_key() -> str:
    key = os.getenv("MARA_API_KEY")
    if key:
        return key
    for line in open(ROOT / ".env", encoding="utf-8"):
        m = re.match(r'\s*MARA_API_KEY\s*=\s*"?([^"\n]+)"?', line)
        if m:
            return m.group(1).strip()
    return ""


def _ontology():
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name="cachesyn",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "year": P(str)}),
        },
        relationships={"REPORTED": RelDef(source="Company", target="FinancialMetric")},
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", "seocho-dev"))
    ap.add_argument("--database", default="cachesynbench")
    ap.add_argument("--dataset", default="examples/finder/datasets/finder_tutorial_subset.json")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--model", default="mara/DeepSeek-V3.1")
    args = ap.parse_args()

    key = _mara_key()
    os.environ["SEOCHO_RESPONSE_CACHE_PATH"] = tempfile.mktemp(suffix=".jsonl")
    os.environ.setdefault("MARA_API_KEY", key)

    from seocho import Seocho
    from seocho.benchmarking import load_finder_cases

    cases = load_finder_cases(ROOT / args.dataset)[: args.limit]
    client = Seocho.local(_ontology(), llm=args.model, graph=args.graph,
                          neo4j_user=args.neo4j_user, neo4j_password=args.neo4j_password, api_key=key)
    gs = client._engine.graph_store
    try:
        gs.ensure_database(args.database)
    except Exception as exc:  # noqa: BLE001
        print(f"(ensure_database: {type(exc).__name__}: {str(exc)[:80]})")

    def run(session, label) -> List[Tuple[float, str]]:
        rows: List[Tuple[float, str]] = []
        for c in cases:
            t0 = time.perf_counter()
            try:
                session.ask(c.question, database=args.database)
            except Exception:  # noqa: BLE001
                pass
            dt = (time.perf_counter() - t0) * 1000
            mode = session.context.queries[-1]["mode"] if session.context.queries else "?"
            rows.append((dt, mode))
            print(f"  [{label}] {dt:8.1f} ms  mode={mode}")
        return rows

    try:
        for c in cases:
            client.add(c.text, database=args.database, category=str(c.category or "memory"))
        print("=== cold (compute + persist) ===")
        cold = run(client.session(name="cold", database=args.database), "cold")
        print("=== warm (fresh session, persistent-cache hit) ===")
        warm = run(client.session(name="warm", database=args.database), "warm")

        s = summarize_cache_run(cold, warm)
        print("\n" + "=" * 60)
        print("SYNERGY #1 — persistent cache hit-rate + latency win")
        print("=" * 60)
        print(f"  cross-session cache_hit_rate: {s['cache_hit_rate']*100:.0f}%  (n={int(s['n'])})")
        print(f"  cold  p50={s['cold_p50_ms']:.0f}ms  p99={s['cold_p99_ms']:.0f}ms")
        print(f"  warm  p50={s['warm_p50_ms']:.1f}ms  p99={s['warm_p99_ms']:.1f}ms")
        print(f"  warm/cold p99 ratio = {s['warm_over_cold_p99_ratio']:.4f}x  "
              f"({'<0.5x target MET' if s['meets_0_5x_target'] else 'above target'})")
    finally:
        try:
            gs.query("MATCH (n) WHERE n._source_id IS NOT NULL DETACH DELETE n", database=args.database)
        except Exception:  # noqa: BLE001
            pass
        client.close()


if __name__ == "__main__":
    main()
