#!/usr/bin/env python3
"""SRA probe — Slot-Resolution Accuracy (ADR-0103, slice S6).

The other multiplicand of structured retrieval. With DCC = 1.00 (S2), the
end-to-end structured-retrieval hit-rate is bounded only by how well the LLM
decompositions resolve to the correct closed-vocab slots: SRHR ≈ SRA × DCC.

For each prior-resistant SEC question this runs the MARA decomposer + bge slot
resolution (NO graph writes, NO OpenAI) and compares the resolved
(concept_id, entity_cik, period_key) to the gold the dataset already knows.
Reports per-slot accuracy and JOINT accuracy (all three correct = a structured
hit, given DCC=1).

Usage::

    MARA_API_KEY=... PYTHONPATH=src:extraction \\
      python scripts/benchmarks/sra_probe.py --limit-tickers 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench

from seocho.query.semantic_decompose import decompose
from seocho.semantic_layer import (
    EntityResolver,
    default_registry,
    normalize_period,
)


def _gold(row, registry, cik_by_ticker):
    return {
        "concept_id": registry.resolve(row["metric"].replace("_", " ")),
        "entity_cik": cik_by_ticker.get(row["ticker"].upper()),
        "period_key": normalize_period(f"FY{row['fiscal_year']}"),
    }


def run(dataset_path: str, *, limit_tickers, provider, model, use_bge):
    from seocho.store.llm import create_llm_backend

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
    # Seed entity name aliases from the dataset's gold company names so that
    # "Apple Inc." (the entity_surface the LLM will copy) resolves to the CIK.
    company_name_by_ticker = {}
    for r in rows:
        company_name_by_ticker.setdefault(r["ticker"].upper(), r.get("gold_entities", [""])[0])
    resolver = EntityResolver.from_ticker_map(cik_by_ticker, company_name_by_ticker)

    registry = default_registry()
    scorer = None
    if use_bge:
        from seocho.query.embedding_grounding import make_fastembed_scorer
        scorer = make_fastembed_scorer()
        print(f"bge scorer: {'on' if scorer else 'unavailable -> lexical fallback'}",
              file=sys.stderr)

    llm = create_llm_backend(provider=provider, model=model)

    records: List[Dict[str, Any]] = []
    for r in rows:
        gold = _gold(r, registry, cik_by_ticker)
        qs, slots = decompose(r["question"], llm=llm, registry=registry,
                              resolver=resolver, scorer=scorer)
        got_period = slots.period_keys[0] if slots.period_keys else None
        rec = {
            "ticker": r["ticker"], "metric": r["metric"], "fiscal_year": r["fiscal_year"],
            "concept_ok": slots.concept_id == gold["concept_id"] and bool(gold["concept_id"]),
            "entity_ok": slots.entity_cik == gold["entity_cik"] and bool(gold["entity_cik"]),
            "period_ok": got_period == gold["period_key"] and bool(gold["period_key"]),
            "decompose_ok": qs is not None,
            "got": {"concept": slots.concept_id, "cik": slots.entity_cik, "period": got_period},
            "gold": gold,
        }
        rec["joint_ok"] = rec["concept_ok"] and rec["entity_ok"] and rec["period_ok"]
        records.append(rec)
        print(f"  [{r['ticker']} {r['metric']} FY{r['fiscal_year']}] "
              f"c={'Y' if rec['concept_ok'] else 'n'} "
              f"e={'Y' if rec['entity_ok'] else 'n'} "
              f"p={'Y' if rec['period_ok'] else 'n'} "
              f"joint={'Y' if rec['joint_ok'] else 'n'}", file=sys.stderr)

    n = len(records)
    def rate(k): return round(sum(x[k] for x in records) / n, 3) if n else None
    summary = {
        "n": n,
        "concept_acc": rate("concept_ok"),
        "entity_acc": rate("entity_ok"),
        "period_acc": rate("period_ok"),
        "decompose_acc": rate("decompose_ok"),
        "joint_sra": rate("joint_ok"),
        "note": "SRHR ~= joint_sra x DCC; DCC measured 1.00 (S2)",
    }
    return {"config": {"provider": provider, "model": model, "bge": bool(scorer),
                       "tickers": tickers},
            "summary": summary, "records": records}


def main() -> int:
    p = argparse.ArgumentParser(description="SRA probe (ADR-0103 S6)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--limit-tickers", type=int, default=None)
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--no-bge", action="store_true", help="use lexical scorer instead of bge")
    p.add_argument("--out", default="-")
    args = p.parse_args()

    result = run(args.dataset, limit_tickers=args.limit_tickers,
                 provider=args.provider, model=args.model, use_bge=not args.no_bge)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== SRA (MARA decompose + bge resolve) === {result['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
