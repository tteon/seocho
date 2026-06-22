#!/usr/bin/env python3
"""SRHR probe — closed-loop end-to-end Structured-Retrieval Hit-Rate (ADR-0103 S8).

The capstone confirmation. S2 measured DCC=1.00 (oracle slots) and S6 measured
SRA=1.00 (decompose, no execution) SEPARATELY; this closes the loop: seed
reified Observations, then for every prior-resistant SEC question run the FULL
semantic lane (decompose → arbitrate → compile → execute → format) and score the
returned answer against the deterministic XBRL gold.

Fallback-OFF by construction: semantic_answer never touches the chunk fallback,
so SRHR here is pure STRUCTURED graph contribution (the metric the whole session
could not previously isolate). No OpenAI (MARA decompose + bge resolve).

Usage::

    MARA_API_KEY=... PYTHONPATH=src:extraction \\
      python scripts/benchmarks/srhr_probe.py --out outputs/evaluation/sec_temporal/srhr.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench
from dcc_probe import seed_observations, _WS
from sec_temporal_run import value_matches

from seocho.index.observation_writer import ensure_observation_constraint
from seocho.query.semantic_query import semantic_answer
from seocho.semantic_layer import EntityResolver, default_registry


def run(dataset_path, *, limit_tickers, database, uri, user, password, provider, model, use_bge):
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    rows = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    tickers = sorted({r["ticker"] for r in rows})
    if limit_tickers:
        tickers = tickers[:limit_tickers]
    rows = [r for r in rows if r["ticker"] in set(tickers)]
    cik_by_ticker = bench.resolve_ciks(tickers)
    name_by_ticker = {}
    for r in rows:
        name_by_ticker.setdefault(r["ticker"].upper(), r.get("gold_entities", [""])[0])
    resolver = EntityResolver.from_ticker_map(cik_by_ticker, name_by_ticker)
    registry = default_registry()

    scorer = None
    if use_bge:
        from seocho.query.embedding_grounding import make_fastembed_scorer
        scorer = make_fastembed_scorer()

    gs = Neo4jGraphStore(uri=uri, user=user, password=password)
    with gs._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)
    seeded = seed_observations(gs, database, rows, cik_by_ticker, registry)
    ensure_observation_constraint(gs, database)
    print(f"seeded {seeded} observations across {len(tickers)} tickers", file=sys.stderr)

    llm = create_llm_backend(provider=provider, model=model)
    records: List[Dict[str, Any]] = []
    for r in rows:
        sr = semantic_answer(r["question"], llm=llm, graph_store=gs, database=database,
                             workspace_id=_WS, registry=registry, resolver=resolver,
                             scorer=scorer)
        hit = sr.route == "STRUCTURED" and sr.answer is not None and \
            value_matches(sr.answer, r["raw_value"])
        records.append({"ticker": r["ticker"], "metric": r["metric"],
                        "fiscal_year": r["fiscal_year"], "prior_stale": r["prior_stale"],
                        "route": sr.route, "answer": sr.answer, "gold": r["answer"],
                        "srhr_hit": hit})
        print(f"  [{r['ticker']} {r['metric']} FY{r['fiscal_year']}] "
              f"route={sr.route} hit={'Y' if hit else 'n'} ans={sr.answer!r}", file=sys.stderr)
    try:
        gs.close()
    except Exception:
        pass

    n = len(records)
    stale = [x for x in records if x["prior_stale"]]
    return {
        "config": {"fallback": "OFF (structured-only)", "provider": provider,
                   "model": model, "bge": bool(scorer), "seeded": seeded},
        "summary": {
            "n": n,
            "srhr": round(sum(x["srhr_hit"] for x in records) / n, 3) if n else None,
            "stale_srhr": round(sum(x["srhr_hit"] for x in stale) / len(stale), 3) if stale else None,
            "routes": dict(Counter(x["route"] for x in records)),
            "note": "fallback-OFF; SRHR = pure STRUCTURED graph contribution",
        },
        "records": records,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="SRHR closed-loop probe (ADR-0103 S8)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--limit-tickers", type=int, default=None)
    p.add_argument("--database", default="srhrprobe")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--no-bge", action="store_true")
    p.add_argument("--out", default="-")
    args = p.parse_args()
    result = run(args.dataset, limit_tickers=args.limit_tickers, database=args.database,
                 uri=args.uri, user=args.user, password=args.password,
                 provider=args.provider, model=args.model, use_bge=not args.no_bge)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== SRHR (closed-loop, fallback-OFF) === {result['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
