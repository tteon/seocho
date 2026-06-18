#!/usr/bin/env python3
"""Item-8 table ingestion e2e (ADR-0103, slice S11) — lift the MD&A 0.00 floor.

The honest real-noise test. On real 10-K MD&A *narrative*, grounded scored 0.00
because the figures live in the Item-8 financial-statement TABLES, not prose.
This harness parses those tables (extract_table_facts), ingests them as reified
:Observation nodes, then runs the semantic lane (decompose→arbitrate→compile→
execute) and scores SRHR against the XBRL gold — measuring how far real table
extraction lifts the floor.

Coverage is bounded by table-layout variety (best-effort parsing), so the
honest result is 0.00 < SRHR < 1.00: real extraction noise, the thing the
synthetic benchmark could not show. MARA + bge only.

Usage::

    MARA_API_KEY=... PYTHONPATH=src:extraction \\
      python scripts/benchmarks/sec_table_run.py --tickers AAPL,MSFT,NVDA
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
import sec_filing_text as ft
import sec_temporal_bench as bench
from sec_temporal_run import value_matches

from seocho.index.observation_writer import ensure_observation_constraint
from seocho.query.semantic_query import semantic_answer
from seocho.semantic_layer import (
    EntityResolver,
    Period,
    default_registry,
    observation_key,
)

_WS = "sec-table"


def _seed_table_facts(gs, database, cik, facts) -> int:
    """MERGE reified Company + Observation rows from extracted TableFacts."""
    written = 0
    with gs._driver.session(database=database) as s:
        for f in facts:
            period_key = Period(fiscal_year=f.fiscal_year).key   # fiscal:YYYY:FY
            obs_id = observation_key(entity_key=cik, concept_id=f.concept_id,
                                     period_key=period_key, unit=f.unit, workspace_id=_WS)
            s.run(
                "MERGE (c:Company {cik:$cik, _workspace_id:$ws}) "
                "MERGE (o:Observation {obs_id:$obs_id}) "
                "SET o.concept_id=$concept_id, o.entity_cik=$cik, o.period_key=$pk, "
                "    o.value_num=$val, o.unit=$unit, o.basis='consolidated', "
                "    o.workspace_id=$ws, o._workspace_id=$ws "
                "MERGE (c)-[:HAS_OBSERVATION]->(o)",
                cik=cik, obs_id=obs_id, concept_id=f.concept_id, pk=period_key,
                val=float(f.value_num), unit=f.unit, ws=_WS,
            )
            written += 1
    return written


def run(dataset_path, tickers, *, database, uri, user, password, provider, model):
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend
    from seocho.query.embedding_grounding import make_fastembed_scorer

    rows = []
    with Path(dataset_path).open("r", encoding="utf-8") as f:
        for l in f:
            if l.strip():
                rows.append(json.loads(l))
    rows = [r for r in rows if r["ticker"] in set(tickers)]
    cik_by_ticker = bench.resolve_ciks(tickers)
    name_by_ticker = {r["ticker"].upper(): r.get("gold_entities", [""])[0] for r in rows}
    resolver = EntityResolver.from_ticker_map(cik_by_ticker, name_by_ticker)
    registry = default_registry()
    scorer = make_fastembed_scorer()

    gs = Neo4jGraphStore(uri=uri, user=user, password=password)
    with gs._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)
    with gs._driver.session(database=database) as s:
        s.run("MATCH (n) DETACH DELETE n")
    ensure_observation_constraint(gs, database)

    extracted = {}
    for t in tickers:
        cik = cik_by_ticker.get(t.upper())
        if not cik:
            continue
        filing = ft.latest_10k(t.upper(), cik)
        facts = ft.fetch_table_facts(filing, registry=registry) if filing else []
        n = _seed_table_facts(gs, database, cik, facts)
        extracted[t] = {"facts": len(facts),
                        "concepts": sorted({f.concept_id for f in facts}),
                        "years": sorted({f.fiscal_year for f in facts})}
        print(f"  [{t}] 10-K {filing.report_date if filing else '?'}: "
              f"{len(facts)} table facts seeded", file=sys.stderr)

    llm = create_llm_backend(provider=provider, model=model)
    records: List[Dict[str, Any]] = []
    for r in rows:
        sr = semantic_answer(r["question"], llm=llm, graph_store=gs, database=database,
                             workspace_id=_WS, registry=registry, resolver=resolver,
                             scorer=scorer)
        hit = sr.route == "STRUCTURED" and sr.answer is not None and \
            value_matches(sr.answer, r["raw_value"])
        records.append({"ticker": r["ticker"], "metric": r["metric"],
                        "fiscal_year": r["fiscal_year"], "route": sr.route,
                        "answer": sr.answer, "gold": r["answer"], "hit": hit})
        print(f"    [{r['ticker']} {r['metric']} FY{r['fiscal_year']}] "
              f"route={sr.route} hit={'Y' if hit else 'n'}", file=sys.stderr)
    try:
        gs.close()
    except Exception:
        pass

    n = len(records)
    return {
        "config": {"corpus": "real 10-K Item-8 tables", "tickers": tickers,
                   "provider": provider, "model": model},
        "extracted": extracted,
        "summary": {
            "n": n,
            "table_srhr": round(sum(x["hit"] for x in records) / n, 3) if n else None,
            "routes": dict(Counter(x["route"] for x in records)),
            "baseline_mdna_grounded": 0.0,
            "note": "vs MD&A-prose grounded 0.00; coverage bounded by table layout",
        },
        "records": records,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Item-8 table ingestion e2e (ADR-0103 S11)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--tickers", default="AAPL,MSFT,NVDA")
    p.add_argument("--database", default="sectable")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--out", default="-")
    args = p.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    result = run(args.dataset, tickers, database=args.database, uri=args.uri,
                 user=args.user, password=args.password, provider=args.provider,
                 model=args.model)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== Item-8 table e2e === {result['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
