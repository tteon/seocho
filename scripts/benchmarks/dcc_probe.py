#!/usr/bin/env python3
"""DCC probe — Deterministic-Compile-Correctness gate (ADR-0103, slice S2).

The cheapest, most diagnostic experiment in the redesign: with NO LLM, seed
reified :Observation nodes into DozerDB straight from the prior-resistant SEC
gold, then for each question feed ORACLE slots (the known-correct
entity_cik / concept_id / period_key) into `compile_observation_lookup` and run
the exact-key Cypher. DCC = fraction where the returned typed `value_num`
matches the gold.

This isolates the graph schema + compiler from the LLM decomposer. If DCC is
high, the redesign thesis holds — the bottleneck was free-text identity, not
Cypher, and the existing query machinery "just works" once the data model is
deterministically addressable. If DCC is low, stop and fix the
schema/templates before any LLM work (run-order step 1; gate DCC >= 0.95).

No OpenAI, no MARA — this probe touches neither; it is pure graph + structure.

Usage::

    PYTHONPATH=src python scripts/benchmarks/dcc_probe.py \\
      --dataset outputs/evaluation/sec_temporal/dataset.jsonl --limit-tickers 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench  # resolve_ciks

from seocho.semantic_layer import (
    ObservationSlots,
    compile_observation_lookup,
    default_registry,
    normalize_period,
    observation_key,
)

_WS = "dcc-probe"


def _metric_to_concept(registry, metric: str):
    # dataset metric is "revenue" / "net_income"; resolve via the closed vocab
    return registry.resolve(metric.replace("_", " "))


def seed_observations(graph_store, database: str, rows: List[Dict[str, Any]],
                      cik_by_ticker: Dict[str, str], registry) -> int:
    """Write reified Company + Observation (MERGE on obs_id) from gold facts."""
    written = 0
    with graph_store._driver.session(database=database) as s:
        s.run("MATCH (n) DETACH DELETE n")
        for r in rows:
            cik = cik_by_ticker.get(r["ticker"].upper())
            concept_id = _metric_to_concept(registry, r["metric"])
            period_key = normalize_period(f"FY{r['fiscal_year']}")
            if not (cik and concept_id and period_key):
                continue
            obs_id = observation_key(entity_key=cik, concept_id=concept_id,
                                     period_key=period_key, unit=r.get("unit", "USD"),
                                     workspace_id=_WS)
            s.run(
                "MERGE (c:Company {cik: $cik, _workspace_id: $ws}) "
                "SET c.name = $name "
                "MERGE (o:Observation {obs_id: $obs_id}) "
                "SET o.concept_id=$concept_id, o.entity_cik=$cik, "
                "    o.period_key=$period_key, o.period_end=$period_end, "
                "    o.value_num=$value_num, o.unit=$unit, o.basis='consolidated', "
                "    o.workspace_id=$ws, o._workspace_id=$ws "
                "MERGE (c)-[:HAS_OBSERVATION]->(o)",
                cik=cik, name=r["ticker"].upper(), obs_id=obs_id,
                concept_id=concept_id, period_key=period_key,
                period_end=r.get("period_end", ""), value_num=float(r["raw_value"]),
                unit=r.get("unit", "USD"), ws=_WS,
            )
            written += 1
    return written


def run(dataset_path: str, *, limit_tickers, database, uri, user, password):
    from seocho.store.graph import Neo4jGraphStore

    rows = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for l in f:
            if l.strip():
                rows.append(json.loads(l))
    tickers = sorted({r["ticker"] for r in rows})
    if limit_tickers:
        tickers = tickers[:limit_tickers]
    rows = [r for r in rows if r["ticker"] in set(tickers)]
    cik_by_ticker = bench.resolve_ciks(tickers)
    registry = default_registry()

    graph_store = Neo4jGraphStore(uri=uri, user=user, password=password)
    with graph_store._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)

    written = seed_observations(graph_store, database, rows, cik_by_ticker, registry)
    print(f"seeded {written} observations across {len(tickers)} tickers", file=sys.stderr)

    records: List[Dict[str, Any]] = []
    for r in rows:
        cik = cik_by_ticker.get(r["ticker"].upper())
        concept_id = _metric_to_concept(registry, r["metric"])
        period_key = normalize_period(f"FY{r['fiscal_year']}")
        if not (cik and concept_id and period_key):
            records.append({**_slim(r), "skipped": True, "correct": False})
            continue
        slots = ObservationSlots(entity_cik=cik, concept_id=concept_id,
                                 period_keys=(period_key,), unit=r.get("unit", "USD"))
        cypher, params = compile_observation_lookup(slots, workspace_id=_WS)
        result = graph_store.query(cypher, params=params, database=database)
        got = float(result[0]["value"]) if result else None
        correct = got is not None and abs(got - float(r["raw_value"])) < 1.0
        records.append({**_slim(r), "skipped": False, "rows": len(result or []),
                        "got": got, "gold": float(r["raw_value"]), "correct": correct})

    try:
        graph_store.close()
    except Exception:
        pass

    scored = [x for x in records if not x["skipped"]]
    dcc = round(sum(x["correct"] for x in scored) / len(scored), 3) if scored else None
    return {
        "config": {"dataset": dataset_path, "tickers": tickers, "seeded": written,
                   "llm": "none (pure structure probe)"},
        "dcc": dcc, "scored": len(scored), "skipped": len(records) - len(scored),
        "records": records,
    }


def _slim(r):
    return {"ticker": r["ticker"], "metric": r["metric"], "fiscal_year": r["fiscal_year"]}


def main() -> int:
    p = argparse.ArgumentParser(description="DCC probe (ADR-0103 S2)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--limit-tickers", type=int, default=None)
    p.add_argument("--database", default="dccprobe")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--out", default="-")
    args = p.parse_args()

    result = run(args.dataset, limit_tickers=args.limit_tickers, database=args.database,
                 uri=args.uri, user=args.user, password=args.password)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== DCC (oracle slots, no LLM) = {result['dcc']} "
          f"({result['scored']} scored, {result['skipped']} skipped) ===", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
