#!/usr/bin/env python3
"""SEC MD&A e2e — grounding on REAL 10-K narrative (the harder corpus).

The follow-up to sec_temporal_run: instead of clean XBRL fact sentences, index
the actual Item-7 MD&A narrative from each company's most recent 10-K, then ask
the same temporal questions. This is where extraction + retrieval noise is real
and the synthetic-corpus 1.00 ceiling is expected to drop — especially for
metrics (e.g. net income) whose exact figure lives in the financial statements
(Item 8), not the MD&A prose.

Reuses the deterministic value matching / temporal verdict / aggregation from
sec_temporal_run, and adds a per-metric answerability breakdown (the key MD&A
finding) plus a gold-in-corpus probe (did the indexed narrative even contain
the figure?).

Usage::

    SEOCHO_CHUNK_FALLBACK=1 PYTHONPATH=src:extraction \\
      python scripts/benchmarks/sec_filing_run.py \\
      --tickers AAPL,MSFT,NVDA --out outputs/evaluation/sec_temporal/mdna_run.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import sec_filing_text as ft
import sec_temporal_bench as bench
import sec_temporal_run as tr


def _per_metric(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    by_metric: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_metric[r["metric"]].append(r)
    for metric, rows in by_metric.items():
        n = len(rows)
        out[metric] = {
            "n": n,
            "grounded_acc": round(sum(r["grounded_match"] for r in rows) / n, 3),
            "gold_in_corpus_rate": round(sum(r["gold_in_corpus"] for r in rows) / n, 3),
            "closed_book_acc": round(sum(r["closed_book_match"] for r in rows) / n, 3),
        }
    return out


def run(dataset_path: str, tickers: List[str], *, database: str, uri: str,
        user: str, password: str, provider: str, model: str) -> Dict[str, Any]:
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    rows = []
    with Path(dataset_path).open("r", encoding="utf-8") as f:
        for l in f:
            if l.strip():
                rows.append(json.loads(l))
    ciks = bench.resolve_ciks(tickers)

    llm = create_llm_backend(provider=provider, model=model)
    graph_store = Neo4jGraphStore(uri=uri, user=user, password=password)
    with graph_store._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)
    with graph_store._driver.session(database=database) as s:
        s.run("MATCH (n) DETACH DELETE n")

    ontology = tr._finance_ontology()
    records: List[Dict[str, Any]] = []

    for ticker in tickers:
        cik = ciks.get(ticker.upper())
        t_rows = [r for r in rows if r["ticker"] == ticker.upper()]
        if not cik or not t_rows:
            print(f"  [skip] {ticker}: cik={cik} rows={len(t_rows)}", file=sys.stderr)
            continue
        filing = ft.latest_10k(ticker.upper(), cik)
        if not filing:
            print(f"  [skip] {ticker}: no 10-K", file=sys.stderr)
            continue
        mdna = ft.fetch_mdna(filing)
        chunks = ft.chunk_text(mdna)
        print(f"  [{ticker}] 10-K {filing.report_date}: MD&A {len(mdna)} chars, "
              f"{len(chunks)} chunks", file=sys.stderr)

        ws = f"mdna_{ticker}".lower().replace("-", "_")
        client = Seocho(ontology=ontology, graph_store=graph_store, llm=llm, workspace_id=ws)
        for ch in chunks:
            try:
                client.add(content=ch, database=database, category="mdna")
            except Exception as exc:  # noqa: BLE001
                print(f"    [ingest-fail] {ws}: {exc}", file=sys.stderr)

        years_in_set = {r["fiscal_year"]: r["raw_value"] for r in t_rows}
        for r in t_rows:
            # did the narrative even contain the figure? (answerability ceiling)
            gold_in_corpus = tr.value_matches(mdna, r["raw_value"])
            try:
                cb = llm.complete(system=tr.CLOSED_BOOK_SYSTEM, user=r["question"]).text
            except Exception as exc:  # noqa: BLE001
                cb = f"<error: {exc}>"
            try:
                gr = client.ask(r["question"], database=database)
            except Exception as exc:  # noqa: BLE001
                gr = f"<error: {exc}>"
            others = [v for y, v in years_in_set.items() if y != r["fiscal_year"]]
            rec = {
                "ticker": ticker.upper(), "metric": r["metric"],
                "fiscal_year": r["fiscal_year"], "prior_stale": r["prior_stale"],
                "gold": r["answer"], "raw_value": r["raw_value"],
                "gold_in_corpus": gold_in_corpus,
                "closed_book_match": tr.value_matches(cb, r["raw_value"]),
                "grounded_match": tr.value_matches(gr, r["raw_value"]),
                "temporal": tr.temporal_verdict(gr, r["raw_value"], others),
                "grounded": gr.strip()[:300],
            }
            records.append(rec)
            print(f"    [{ticker} {r['metric']} FY{r['fiscal_year']}] "
                  f"gold_in_mdna={'Y' if gold_in_corpus else 'n'} "
                  f"gr={'Y' if rec['grounded_match'] else 'n'} "
                  f"temporal={rec['temporal']}", file=sys.stderr)

    try:
        graph_store.close()
    except Exception:
        pass

    return {
        "config": {"corpus": "real 10-K MD&A (Item 7)", "tickers": tickers,
                   "provider": provider, "model": model,
                   "chunk_fallback": os.environ.get("SEOCHO_CHUNK_FALLBACK", "")},
        "summary": tr.aggregate(records),
        "per_metric": _per_metric(records),
        "records": records,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="SEC MD&A e2e runner")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--tickers", default="AAPL,MSFT,NVDA")
    p.add_argument("--database", default="secmdna")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="neo4jpassword")
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--out", default="-")
    args = p.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    result = run(args.dataset, tickers, database=args.database, uri=args.uri,
                 user=args.user, password=args.password,
                 provider=args.provider, model=args.model)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote results to {args.out}", file=sys.stderr)
    print("\n=== MD&A e2e ===", file=sys.stderr)
    print(f"summary:    {result['summary']['closed_book_vs_grounded']}", file=sys.stderr)
    print(f"per-metric: {result['per_metric']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
