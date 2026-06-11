#!/usr/bin/env python3
"""Intent-gated router: question -> required categories/concepts (seocho-tgi).

The foundation for the agent-design bake-off (epic seocho-a3j). An agent must
decide WHICH layer to look at — instance (workspace/db), schema (which FinDER
categories / ConceptRegistry concepts), data (which observations). This routes
the SCHEMA decision: from the question, infer the required FinDER categories
(Financials / Company overview / Footnotes) and finance concepts, so retrieval
can be GATED (the experiment-derived rule: fetch >=2 categories only when the
question needs them — PR #201/#206; single-category over-fetch dilutes).

Rule-based first (per docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md: "rule-based intent
hints; narrow prompt-assisted only when rules are insufficient; do not build a
large router taxonomy before the minimal causal path works"). An optional MARA
fallback fires only when the rules are unsure (no/!=1 strong category).

Validate: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_intent_router.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_backbone import select_xcat_cases  # noqa: E402
from seocho.semantic_layer.concepts import default_registry  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

CATEGORIES = ["Financials", "Company overview", "Footnotes"]

# Category lexicons (rule-based intent hints). Financials is also reinforced by
# the ConceptRegistry surfaces (revenue/margin/EPS/...) added at import time.
_LEX: Dict[str, Set[str]] = {
    "Financials": {
        "revenue", "income", "margin", "eps", "earnings", "ratio", "ratios",
        "growth", "cash", "flow", "leverage", "operating", "profit", "profitability",
        "sales", "cost", "costs", "expense", "debt", "asset", "assets", "liability",
        "dividend", "dividends", "buyback", "buybacks", "capital", "net", "diluted",
        "%", "financial", "liquidity", "interest", "shareholder",
    },
    "Company overview": {
        "headcount", "employee", "employees", "fte", "pte", "workforce", "strategy",
        "business", "segment", "segments", "overview", "scaling", "comp", "hr",
        "allocation", "geographic", "split", "us", "non-us", "headquarters",
        "product", "market", "competition", "operations",
    },
    "Footnotes": {
        "footnote", "footnotes", "note", "notes", "disclosure", "disclosures",
        "policy", "policies", "pledge", "pledges", "pledged", "accounting",
        "contingency", "contingencies", "commitment", "commitments", "lease",
        "leases", "restrict", "restricts", "restriction", "flexibility",
        "guarantee", "guarantees", "off-balance", "recognition", "impairment",
    },
}

# reinforce Financials with the concept registry's pref/alt labels
for _c in default_registry().concepts:
    for _s in (_c.pref_label, *getattr(_c, "alt_labels", ())):
        for _tok in re.findall(r"[a-z]+", str(_s).lower()):
            _LEX["Financials"].add(_tok)

_THRESHOLD = 1   # >=1 lexicon hit gates a category in


@dataclass
class IntentSpec:
    question: str
    required_categories: List[str]
    scores: Dict[str, int] = field(default_factory=dict)
    used_fallback: bool = False
    is_cross_category: bool = False


def _score(question: str) -> Dict[str, int]:
    toks = set(re.findall(r"[a-z%\-]+", question.lower()))
    return {cat: len(toks & lex) for cat, lex in _LEX.items()}


def route(question: str, *, llm=None, llm_model: str = "") -> IntentSpec:
    """Rule-based schema routing; optional MARA fallback when rules are unsure."""
    scores = _score(question)
    gated = [c for c in CATEGORIES if scores[c] >= _THRESHOLD]
    used_fallback = False
    # rules unsure: nothing gated, or everything tied at the floor -> ask the LLM
    if llm is not None and (not gated or max(scores.values(), default=0) < _THRESHOLD):
        cats = _llm_route(question, llm, llm_model)
        if cats:
            gated, used_fallback = cats, True
    if not gated:                       # safe default: the dominant FinDER lane
        gated = ["Financials"]
    return IntentSpec(question=question, required_categories=sorted(gated),
                      scores=scores, used_fallback=used_fallback,
                      is_cross_category=len(gated) >= 2)


def _llm_route(question: str, llm, model: str) -> Optional[List[str]]:
    sys_p = ("Classify which 10-K sections a question needs. Reply with a comma "
             "list from exactly: Financials, Company overview, Footnotes. "
             "Output only the list.")
    try:
        r = llm.chat.completions.create(
            model=model, temperature=0, max_tokens=400,
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": question}])
        txt = (r.choices[0].message.content or "")
        return [c for c in CATEGORIES if c.lower() in txt.lower()] or None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Validation: routing accuracy vs gold-required categories (deterministic)
# --------------------------------------------------------------------------
def _evaluate(router, cases) -> dict:
    """Routing accuracy of a `router(question)->set[str]` over single + B questions."""
    from itertools import combinations
    recall = pnum = pden = over = 0
    for c in cases:
        got = router(c.query)
        if {c.category} & got:
            recall += 1
        pnum += len({c.category} & got)
        pden += len(got) if got else 1
        if len(got) >= 2:
            over += 1
    by_cik: Dict[str, list] = {}
    for c in cases:
        by_cik.setdefault(c.cik, []).append(c)
    both = bq = 0
    for cik, cs in by_cik.items():
        seen = {}
        for c in cs:
            seen.setdefault(c.category, c)
        for a, b in combinations(sorted(seen), 2):
            bq += 1
            if {a, b} <= router(f"{seen[a].query}  ALSO: {seen[b].query}"):
                both += 1
    n = len(cases)
    return {"recall": recall / n, "precision": pnum / pden, "over": over, "n": n,
            "bq_both": both / bq, "bq": bq}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="", help="also evaluate an LLM router, e.g. mara/gpt-oss-120b")
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)

    routers = {"rule-based": lambda q: set(route(q).required_categories)}
    if args.llm:
        try:
            from dotenv import load_dotenv
            load_dotenv(_ROOT / ".env")
        except Exception:
            pass
        from examples.finder.lib import llm_io
        spec = llm_io.parse_llm_spec(args.llm)
        client = llm_io.make_chat_client(spec)
        routers[f"llm({spec.model})"] = lambda q: set(_llm_route(q, client, spec.model) or ["Financials"])

    print("=" * 78)
    print("Intent-gated router — routing accuracy vs gold-required categories")
    print("=" * 78)
    print(f"\n  {'router':<22} {'single recall':>13} {'precision':>10} {'over>=2':>8} "
          f"{'B both-recall':>13}")
    print("  " + "-" * 70)
    for name, r in routers.items():
        m = _evaluate(r, cases)
        print(f"  {name:<22} {m['recall']:>13.2f} {m['precision']:>10.2f} "
              f"{str(m['over'])+'/'+str(m['n']):>8} {m['bq_both']:>13.2f}")
    print("\n  high single precision = no over-fetch (avoids dilution); high B both-recall")
    print("  = detects cross-category need (enables the backbone). The better router is the")
    print("  gate the bake-off archetypes use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
