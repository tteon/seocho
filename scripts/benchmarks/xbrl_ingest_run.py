#!/usr/bin/env python3
"""XBRL companyfacts ingestion e2e (ADR-0103 follow-up).

Ingests SEC XBRL companyfacts into reified :Observation nodes (deterministic, no
LLM, no HTML parsing) and runs the semantic lane to confirm the production path
populates a queryable graph. Contrast with S11 (HTML table scraping, noisy
0.333): XBRL is the structured source, so this is clean — but note gold==source,
so a high SRHR here confirms the INGESTER is correct, it is NOT a benchmark win.

Usage::
    MARA_API_KEY=... PYTHONPATH=src:extraction \\
      python scripts/benchmarks/xbrl_ingest_run.py --tickers AAPL,MSFT,NVDA
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
from sec_temporal_run import value_matches

from seocho.index.observation_writer import ensure_observation_constraint
from seocho.index.xbrl_ingest import companyfacts_to_observations, fetch_companyfacts
from seocho.query.semantic_query import semantic_answer
from seocho.semantic_layer import EntityResolver, default_registry

_WS = "xbrl-ingest"


def _write(gs, database, nodes, rels):
    with gs._driver.session(database=database) as s:
        for n in nodes:
            p = n["properties"]
            if n["label"] == "Company":
                s.run("MERGE (c:Company {cik:$cik, _workspace_id:$ws}) SET c.name=$name",
                      cik=p["cik"], name=p.get("name"), ws=_WS)
            else:
                s.run("MERGE (o:Observation {obs_id:$id}) SET o += $p, o.workspace_id=$ws, "
                      "o._workspace_id=$ws", id=p["obs_id"], p=p, ws=_WS)
        for r in rels:
            s.run("MATCH (c:Company {cik:$cik, _workspace_id:$ws}), "
                  "(o:Observation {obs_id:$oid}) MERGE (c)-[:HAS_OBSERVATION]->(o)",
                  cik=r["source"].split(":", 1)[-1], oid=r["target"], ws=_WS)


def run(dataset_path, tickers, *, database, uri, user, password, provider, model):
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend
    from seocho.query.embedding_grounding import make_fastembed_scorer

    rows = []
    with Path(dataset_path).open("r") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
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

    total_obs = 0
    for t in tickers:
        cik = cik_by_ticker.get(t.upper())
        if not cik:
            continue
        nodes, rels = companyfacts_to_observations(
            fetch_companyfacts(cik), registry=registry, cik=cik,
            workspace_id=_WS, n_years=5, min_fiscal_year=2022)
        _write(gs, database, nodes, rels)
        n_obs = sum(1 for n in nodes if n["label"] == "Observation")
        total_obs += n_obs
        print(f"  [{t}] ingested {n_obs} observations from XBRL companyfacts", file=sys.stderr)

    llm = create_llm_backend(provider=provider, model=model)
    records: List[Dict[str, Any]] = []
    for r in rows:
        sr = semantic_answer(r["question"], llm=llm, graph_store=gs, database=database,
                             workspace_id=_WS, registry=registry, resolver=resolver, scorer=scorer)
        hit = sr.route == "STRUCTURED" and sr.answer is not None and value_matches(sr.answer, r["raw_value"])
        records.append({"ticker": r["ticker"], "metric": r["metric"],
                        "fiscal_year": r["fiscal_year"], "route": sr.route, "hit": hit})
        print(f"    [{r['ticker']} {r['metric']} FY{r['fiscal_year']}] route={sr.route} "
              f"hit={'Y' if hit else 'n'}", file=sys.stderr)
    try:
        gs.close()
    except Exception:
        pass

    n = len(records)
    return {
        "config": {"corpus": "SEC XBRL companyfacts (deterministic ingest)",
                   "tickers": tickers, "note": "gold==source: confirms ingester, NOT a benchmark win"},
        "summary": {"n": n, "observations_ingested": total_obs,
                    "xbrl_srhr": round(sum(x["hit"] for x in records) / n, 3) if n else None,
                    "routes": dict(Counter(x["route"] for x in records)),
                    "s11_html_table_baseline": 0.333},
        "records": records,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="XBRL companyfacts ingest e2e")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--tickers", default="AAPL,MSFT,NVDA")
    p.add_argument("--database", default="xbrlingest")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--out", default="-")
    args = p.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    result = run(args.dataset, tickers, database=args.database, uri=args.uri, user=args.user,
                 password=args.password, provider=args.provider, model=args.model)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== XBRL ingest e2e === {result['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
