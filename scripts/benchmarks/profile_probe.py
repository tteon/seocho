#!/usr/bin/env python3
"""PROFILE-oracle profiler (ADR-0103, slice S12 / GOPTS Layer-1-2).

Confirms the compiled exact-key observation_lookup is not just CORRECT (DCC,
S2) but CHEAP: with the UNIQUE(obs_id) constraint + Company.cik index in place
(ensure_observation_constraint), Neo4j PROFILE should start the plan with a
NodeIndexSeek on Company.cik — NOT an AllNodesScan / NodeByLabelScan over every
Company or Observation. The harness seeds the graph, PROFILEs the compiled
Cypher for oracle slots, walks the plan operators, and asserts seek-not-scan +
reports total db_hits.

No LLM (no MARA/OpenAI) — pure graph + structure.

Usage::

    PYTHONPATH=src python scripts/benchmarks/profile_probe.py --limit-tickers 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench
from dcc_probe import seed_observations, _WS

from seocho.index.observation_writer import ensure_observation_constraint
from seocho.semantic_layer import (
    ObservationSlots,
    compile_observation_lookup,
    default_registry,
    normalize_period,
)

_SCAN_OPS = ("AllNodesScan", "NodeByLabelScan")
_SEEK_OPS = ("NodeIndexSeek", "NodeUniqueIndexSeek", "NodeIndexSeekByRange")


def _walk(plan, ops: List[str], hits: List[int]):
    # operator names are db-qualified ("NodeIndexSeek@dbname") — strip the suffix
    raw = plan.get("operatorType") or plan.get("operator_type") or ""
    ops.append(str(raw).split("@", 1)[0])
    args = plan.get("arguments", {}) or {}
    dbh = args.get("DbHits") or args.get("dbHits") or plan.get("dbHits") or 0
    try:
        hits.append(int(dbh))
    except (TypeError, ValueError):
        pass
    for child in plan.get("children", []) or []:
        _walk(child, ops, hits)


def _profile(graph_store, database, cypher, params):
    with graph_store._driver.session(database=database) as session:
        result = session.run(f"PROFILE {cypher}", **params)
        rows = list(result)
        summary = result.consume()
        prof = summary.profile or {}
    ops: List[str] = []
    hits: List[int] = []
    if prof:
        _walk(prof, ops, hits)
    return ops, sum(hits), len(rows)


def run(dataset_path, *, limit_tickers, database, uri, user, password):
    from seocho.store.graph import Neo4jGraphStore

    rows = []

    with Path(dataset_path).open("r", encoding="utf-8") as f:

        for l in f:

            if l.strip():

                rows.append(json.loads(l))
    tickers = sorted({r["ticker"] for r in rows})
    if limit_tickers:
        tickers = tickers[:limit_tickers]
    rows = [r for r in rows if r["ticker"] in set(tickers)]
    cik_by_ticker = bench.resolve_ciks(tickers)
    registry = default_registry()

    gs = Neo4jGraphStore(uri=uri, user=user, password=password)
    with gs._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)
    seeded = seed_observations(gs, database, rows, cik_by_ticker, registry)
    ok = ensure_observation_constraint(gs, database)
    print(f"seeded {seeded} observations; constraints+index: {ok}", file=sys.stderr)
    time.sleep(1.0)  # let the index come online

    samples, scan_hits = [], 0
    for r in rows[:10]:  # a handful of representative lookups
        cik = cik_by_ticker.get(r["ticker"].upper())
        concept_id = registry.resolve(r["metric"].replace("_", " "))
        period_key = normalize_period(f"FY{r['fiscal_year']}")
        if not (cik and concept_id and period_key):
            continue
        slots = ObservationSlots(
            entity_cik=cik, concept_id=concept_id, period_keys=(period_key,)
        )
        cypher, params = compile_observation_lookup(slots, workspace_id=_WS)
        ops, db_hits, nrows = _profile(gs, database, cypher, params)
        used_scan = any(o in _SCAN_OPS for o in ops)
        used_seek = any(o in _SEEK_OPS for o in ops)
        scan_hits += int(used_scan)
        samples.append(
            {
                "ticker": r["ticker"],
                "metric": r["metric"],
                "rows": nrows,
                "db_hits": db_hits,
                "seek": used_seek,
                "scan": used_scan,
                "ops": ops,
            }
        )
        print(
            f"  [{r['ticker']} {r['metric']} FY{r['fiscal_year']}] "
            f"rows={nrows} db_hits={db_hits} seek={used_seek} scan={used_scan}",
            file=sys.stderr,
        )
    try:
        gs.close()
    except Exception:
        pass

    n = len(samples)
    return {
        "config": {
            "database": database,
            "seeded": seeded,
            "constraints": ok,
            "llm": "none",
        },
        "summary": {
            "n": n,
            "seek_rate": round(sum(s["seek"] for s in samples) / n, 3) if n else None,
            "scan_count": scan_hits,
            "max_db_hits": max((s["db_hits"] for s in samples), default=0),
            "verdict": (
                "index-backed (seek, no scan)"
                if n and scan_hits == 0
                else "SCAN DETECTED — index not used"
            ),
        },
        "samples": samples,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="PROFILE profiler (ADR-0103 S12)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--limit-tickers", type=int, default=5)
    p.add_argument("--database", default="profileprobe")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--out", default="-")
    args = p.parse_args()
    result = run(
        args.dataset,
        limit_tickers=args.limit_tickers,
        database=args.database,
        uri=args.uri,
        user=args.user,
        password=args.password,
    )
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== PROFILE === {result['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
