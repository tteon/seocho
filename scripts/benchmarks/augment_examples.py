#!/usr/bin/env python3
"""Few-shot augmentation harness (ADR-0103, slice S10).

Bootstraps a masked-alignment few-shot corpus for the semantic lane, MARA + bge
only (no OpenAI):

1. SEED (deterministic): for each SEC question, compile the exact-key Cypher
   from its gold ObservationSlots and store (question, cypher) — guaranteed
   valid, no LLM.
2. AUGMENT (MARA): paraphrase each seed question into several linguistic styles
   (terse / analyst / indirect) so the corpus carries surface variety while the
   masked skeleton stays constant.
3. INDEX (bge): store every example masked + embedded in a FewShotIndex.
4. DEMONSTRATE: a held-out paraphrase retrieves the structurally-matching
   example (the masked skeleton, not the surface tokens) — the Text2SQL-Flow
   masked-alignment payoff.

Usage::

    MARA_API_KEY=... PYTHONPATH=src python scripts/benchmarks/augment_examples.py --limit 6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import sec_temporal_bench as bench

from seocho.query.fewshot import FewShotIndex
from seocho.semantic_layer import (
    EntityResolver,
    ObservationSlots,
    compile_observation_lookup,
    default_registry,
    normalize_period,
)

_PARAPHRASE_SYS = (
    "Rewrite the financial question in 3 different natural styles (terse, "
    "formal-analyst, indirect) WITHOUT changing the company, metric, or fiscal "
    "year. Return ONLY a JSON array of 3 strings."
)


def _gold_slots(row, registry, cik_by_ticker):
    cik = cik_by_ticker.get(row["ticker"].upper())
    concept = registry.resolve(row["metric"].replace("_", " "))
    period = normalize_period(f"FY{row['fiscal_year']}")
    if not (cik and concept and period):
        return None
    return ObservationSlots(entity_cik=cik, concept_id=concept, period_keys=(period,))


def _paraphrase(llm, question) -> List[str]:
    try:
        resp = llm.complete(system=_PARAPHRASE_SYS, user=question, temperature=0.7,
                            response_format={"type": "json_object"})
        import re
        m = re.search(r"\[.*\]", resp.text, re.S)
        arr = json.loads(m.group(0)) if m else []
        return [str(x) for x in arr if isinstance(x, str)][:3]
    except Exception:
        return []


def run(dataset_path, *, limit, provider, model, paraphrase):
    rows = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for l in f:
            if l.strip():
                rows.append(json.loads(l))
    rows = rows[:limit]
    tickers = sorted({r["ticker"] for r in rows})
    cik_by_ticker = bench.resolve_ciks(tickers)
    name_by_ticker = {r["ticker"].upper(): r.get("gold_entities", [""])[0] for r in rows}
    resolver = EntityResolver.from_ticker_map(cik_by_ticker, name_by_ticker)
    registry = default_registry()

    idx = FewShotIndex()  # bge by default; lexical fallback if unavailable
    print(f"few-shot embed backend: {'bge' if idx._embed else 'lexical fallback'}",
          file=sys.stderr)

    llm = None
    if paraphrase:
        from seocho.store.llm import create_llm_backend
        llm = create_llm_backend(provider=provider, model=model)

    n_seed = n_aug = 0
    for r in rows:
        slots = _gold_slots(r, registry, cik_by_ticker)
        if not slots:
            continue
        cypher, _ = compile_observation_lookup(slots, workspace_id="aug")
        company = name_by_ticker.get(r["ticker"].upper(), r["ticker"])
        metric_surface = r["metric"].replace("_", " ")
        period_surface = f"fiscal year {r['fiscal_year']}"
        idx.add(question=r["question"], cypher=cypher, entity=company,
                metric=metric_surface, period=period_surface, slots=slots,
                metadata={"src": "seed", "concept": slots.concept_id})
        n_seed += 1
        for para in (_paraphrase(llm, r["question"]) if llm else []):
            idx.add(question=para, cypher=cypher, entity=company, metric=metric_surface,
                    period=period_surface, slots=slots,
                    metadata={"src": "paraphrase", "concept": slots.concept_id})
            n_aug += 1

    # DEMONSTRATE structure-aware retrieval on a fresh cross-surface query
    demo_q = "Could you tell me Microsoft's net income figure for the 2024 fiscal year?"
    hits = idx.search(demo_q, entity="Microsoft", metric="net income",
                      period="fiscal year 2024", k=3)
    demo = [{"q": h.question[:70], "concept": h.metadata.get("concept"),
             "score": round(s, 3)} for h, s in hits]

    return {
        "config": {"provider": provider, "model": model, "paraphrase": paraphrase,
                   "embed": "bge" if idx._embed else "lexical"},
        "summary": {"seed": n_seed, "augmented": n_aug, "total": len(idx.examples)},
        "demo_query": demo_q, "demo_top3": demo,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Few-shot augmentation (ADR-0103 S10)")
    p.add_argument("--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl")
    p.add_argument("--limit", type=int, default=6)
    p.add_argument("--provider", default="mara")
    p.add_argument("--model", default="MiniMax-M2.5")
    p.add_argument("--no-paraphrase", action="store_true")
    p.add_argument("--out", default="-")
    args = p.parse_args()
    result = run(args.dataset, limit=args.limit, provider=args.provider,
                 model=args.model, paraphrase=not args.no_paraphrase)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(f"\n=== augment === {result['summary']}  demo_top1={result['demo_top3'][:1]}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
