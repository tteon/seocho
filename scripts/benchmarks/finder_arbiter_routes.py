#!/usr/bin/env python3
"""FinDER × arbiter route distribution (Fork B).

Tests, on the SAME 60-case FinDER sample as the graph-as-context sweep, what the
ADR-0103 arbiter does with each question: how much of FinDER does the closed-vocab
semantic layer route STRUCTURED vs NARRATIVE vs CLARIFY vs FAIL — and WHY (which
slot fails). This is the structured-lane lens H3 needs (graph-as-context could not
test it).

Honest design (no graph seeding here):
  - decompose (MARA) → resolve concept (closed MetricConcept registry), entity
    (ticker→CIK via an EDGAR-built resolver, so entity-OOV doesn't masquerade as
    concept-OOV), period.
  - arbitrate with an EMPTY graph probe. So STRUCTURED is 0 BY CONSTRUCTION (no
    Observations seeded); a fully-resolved case routes NARRATIVE("graph lacks").
  - We therefore ALSO report `structured_eligible` = (concept ∧ entity ∧ period
    all resolve) — the cases that WOULD route STRUCTURED if the graph were seeded.
    That eligibility rate is the real coverage number; the dominant gate is the
    2-concept closed vocab (Revenue/NetIncome) vs FinDER's metric breadth.

$0-ish: one MARA decompose call per case (60), no extraction, no embeddings.

Usage:
  MARA_API_KEY=... PYTHONPATH=src:extraction python3 \\
    scripts/benchmarks/finder_arbiter_routes.py --n-per-slice 10 --seed 42 \\
    --out outputs/evaluation/finder_arbiter_routes/routes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT / "extraction", ROOT / "scripts" / "benchmarks"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import finder_4arm_sample as finder  # reuse load_sample + DATASET_CSV
import sec_temporal_bench as bench  # reuse EDGAR resolve_ciks
from seocho.query.arbiter import arbitrate
from seocho.query.semantic_decompose import decompose
from seocho.semantic_layer import EntityResolver, default_registry

_TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)|\b([A-Z]{2,6})\b")


def _candidate_tickers(cases) -> list[str]:
    cands: set[str] = set()
    STOP = {"FY", "EPS", "GAAP", "US", "USD", "AI", "CEO", "CFO", "R&D", "II", "III"}
    for c in cases:
        for m in _TICKER_RE.finditer(c["query"]):
            tok = m.group(1) or m.group(2)
            if tok and tok not in STOP:
                cands.add(tok)
    return sorted(cands)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="FinDER × arbiter route distribution (Fork B)"
    )
    ap.add_argument("--n-per-slice", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--provider", default="mara")
    ap.add_argument("--model", default="MiniMax-M2.5")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    cases = finder.load_sample(args.n_per_slice, args.seed)
    from seocho.store.llm import create_llm_backend

    llm = create_llm_backend(provider=args.provider, model=args.model)
    registry = default_registry()

    # Build a resolver from the tickers that actually appear in the sample
    # (EDGAR) so entity resolution gets a fair shot — isolating concept-OOV as
    # the real gate. Falls back to the seed resolver if EDGAR is unreachable.
    tickers = _candidate_tickers(cases)
    try:
        cik_by_ticker = bench.resolve_ciks(tickers)
    except Exception as exc:  # pragma: no cover (network)
        print(
            f"[warn] EDGAR resolve_ciks failed ({exc}); using seed resolver",
            file=sys.stderr,
        )
        cik_by_ticker = {}
    resolver = EntityResolver.from_ticker_map(cik_by_ticker, {})
    print(
        f"resolver: {len(cik_by_ticker)}/{len(tickers)} candidate tickers → CIK",
        file=sys.stderr,
    )

    rows = []
    routes = Counter()
    by_slice = defaultdict(Counter)
    elig = 0
    for i, c in enumerate(cases, 1):
        try:
            qs, slots = decompose(
                c["query"], llm=llm, registry=registry, resolver=resolver
            )
            hint = arbitrate(slots)  # empty probe → STRUCTURED impossible w/o seeding
            concept_ok = bool(slots.concept_id)
            entity_ok = bool(slots.entity_cik)
            period_ok = bool(slots.period_keys)
            eligible = concept_ok and entity_ok and period_ok
            elig += int(eligible)
            route = hint.route
        except Exception as exc:
            qs = None
            route = "DECOMPOSE_ERROR"
            concept_ok = entity_ok = period_ok = eligible = False
            print(f"  [{i}] {c['case_id']} ERROR {exc}", file=sys.stderr)
        routes[route] += 1
        by_slice[c["slice"]][route] += 1
        rows.append(
            {
                "case_id": c["case_id"],
                "slice": c["slice"],
                "query": c["query"][:120],
                "intent": getattr(qs, "intent", None),
                "metric_surface": getattr(qs, "metric_surface", None),
                "concept_resolved": concept_ok,
                "entity_resolved": entity_ok,
                "period_resolved": period_ok,
                "structured_eligible": eligible,
                "route": route,
            }
        )
        mark = "ELIG" if eligible else "    "
        print(
            f"  [{i}/{len(cases)}] {c['slice']:24} {route:18} {mark} "
            f"c={int(concept_ok)} e={int(entity_ok)} p={int(period_ok)} "
            f"metric={getattr(qs,'metric_surface','?')!r}",
            flush=True,
        )

    n = len(cases)
    summary = {
        "n": n,
        "routes": dict(routes),
        "structured_eligible": elig,
        "structured_eligible_pct": round(100 * elig / n, 1) if n else 0,
        "by_slice": {s: dict(c) for s, c in by_slice.items()},
        "note": (
            "STRUCTURED is 0 by construction (no Observations seeded); "
            "structured_eligible = concept∧entity∧period resolve = would route "
            "STRUCTURED if seeded. Dominant gate is the closed MetricConcept vocab."
        ),
    }
    out = {
        "summary": summary,
        "rows": rows,
        "provider": args.provider,
        "model": args.model,
        "registry_concepts": [c.concept_id for c in registry.concepts],
    }
    text = json.dumps(out, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"\n== wrote {args.out} ==")
    print("\n=== ROUTE DISTRIBUTION ===", json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
