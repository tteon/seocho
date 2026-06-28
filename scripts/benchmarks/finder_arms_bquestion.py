#!/usr/bin/env python3
"""FinDER cross-category B-questions + Spearman bridge (seocho-992 step 4).

The payoff: on questions that genuinely span >=2 categories, does the shared
backbone (which carries both categories) beat the single-category isolated
agent under a TRUSTWORTHY cross-model judge — and does the cheap deterministic
metric (cross-category coverage) PREDICT the expensive LLM judge_score?

A FinDER B-question is synthesized per cross-category company by combining its
two category rows (e.g. UAL Financials 'operating margin' + UAL Footnotes
'asset pledges'); the gold combines both reference answers. The single-category
'isolated' agent structurally MISSES half; the 'backbone' agent covers both.

Arms (fixed budget, fair-eval):
  isolated_one_cat   evidence from ONE of the two required categories only
  backbone_both      evidence from BOTH required categories (the shared backbone)
  backbone_xbrl      both categories' evidence + structured XBRL numbers

Metrics: deterministic cross_category_coverage (fraction of required categories
the arm's context covers) + cross-model judge_score (gpt-oss-120b judging
MiniMax-M2.5) + token_f1. Bridge: Spearman rho(coverage, judge_score) — the
honesty check that the deterministic structural metric predicts answer quality.

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_arms_bquestion.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_backbone import Case, DATASET, select_xcat_cases  # noqa: E402
from finder_arms import ANSWER_SPEC, CONTEXT_BUDGET, answer, judge  # noqa: E402
from finder_arms_xbrl import xbrl_lines  # noqa: E402
from finder_judge import token_f1  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

_GOLD: Dict[str, str] = {}


def _load_gold() -> None:
    if _GOLD:
        return
    with open(DATASET, newline="") as fh:
        for row in csv.DictReader(fh):
            _GOLD[row["_id"]] = row["answer"]


class BQuestion:
    """A synthetic cross-category question built from two single-category rows."""

    def __init__(self, ticker: str, cik: str, a: Case, b: Case):
        _load_gold()
        self.ticker, self.cik = ticker, cik
        self.cat_a, self.cat_b = a.category, b.category
        self.required = {a.category, b.category}
        self.query = f"{a.query}  ALSO: {b.query}"
        self.gold = f"{_GOLD.get(a.case_id, '')}  ||  {_GOLD.get(b.case_id, '')}"
        self.ev_by_cat = {a.category: list(a.evidence), b.category: list(b.evidence)}


def build_bquestions(resolver: EntityResolver) -> List[BQuestion]:
    """One B-question per category PAIR per company. A 3-category company
    (BBWI/SYK/UAL/VRTX) yields C(3,2)=3 B-questions; a 2-category company yields
    1 — maximizing cross-category data points for a more robust Spearman rho."""
    from itertools import combinations

    cases = select_xcat_cases(resolver)
    by_cik: Dict[str, List[Case]] = {}
    for c in cases:
        by_cik.setdefault(c.cik, []).append(c)
    out: List[BQuestion] = []
    for cik, cs in by_cik.items():
        seen_cat: Dict[str, Case] = {}
        for c in cs:
            seen_cat.setdefault(c.category, c)  # first case per category
        for ca, cb in combinations(sorted(seen_cat), 2):
            out.append(BQuestion(cs[0].ticker, cik, seen_cat[ca], seen_cat[cb]))
    return out


# --------------------------------------------------------------------------
# Arm contexts + their deterministic cross-category coverage
# --------------------------------------------------------------------------
def ctx_isolated_one(bq: BQuestion) -> Tuple[str, float]:
    cat = sorted(bq.required)[0]  # only ONE required category
    ctx = "\n\n".join(bq.ev_by_cat.get(cat, []))[:CONTEXT_BUDGET]
    coverage = 1.0 / len(bq.required)  # covers 1 of N required
    return ctx, coverage


def ctx_backbone_both(bq: BQuestion) -> Tuple[str, float]:
    blocks = []
    for cat in sorted(bq.required):
        blocks.append(f"[{cat}]\n" + "\n\n".join(bq.ev_by_cat.get(cat, [])))
    return "\n\n".join(blocks)[:CONTEXT_BUDGET], 1.0


def ctx_backbone_xbrl(bq: BQuestion) -> Tuple[str, float]:
    nums = xbrl_lines(bq.cik)
    remaining = CONTEXT_BUDGET - len(nums) - 40
    both = "\n\n".join(
        f"[{c}]\n" + "\n\n".join(bq.ev_by_cat.get(c, [])) for c in sorted(bq.required)
    )
    block = (
        "STRUCTURED FINANCIALS:\n" + nums + "\n\nNOTES:\n" + both[: max(0, remaining)]
    )
    return block[:CONTEXT_BUDGET], 1.0


ARMS = {
    "isolated_one_cat": ctx_isolated_one,
    "backbone_both": ctx_backbone_both,
    "backbone_xbrl": ctx_backbone_xbrl,
}


def spearman(xs: List[float], ys: List[float]) -> float:
    """Spearman rank correlation (no scipy)."""
    n = len(xs)
    if n < 2:
        return 0.0

    def ranks(v: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="number of B-questions (0 = all)")
    ap.add_argument(
        "--judge",
        default="mara/gpt-oss-120b",
        help="cross-model judge (different from the answerer)",
    )
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    bqs = build_bquestions(resolver)
    sample = bqs if args.n == 0 else bqs[: args.n]

    aspec = llm_io.parse_llm_spec(ANSWER_SPEC)
    jspec = llm_io.parse_llm_spec(args.judge)
    aclient = llm_io.make_chat_client(aspec)
    jclient = llm_io.make_chat_client(jspec)

    print("=" * 86)
    print(
        f"FinDER cross-category B-questions — answerer={aspec.model}, judge={jspec.model}; "
        f"{len(sample)} B-questions"
    )
    print("=" * 86)
    agg: Dict[str, dict] = {a: {"f1": [], "judge": [], "cov": []} for a in ARMS}
    cov_points: List[float] = []
    judge_points: List[float] = []
    for i, bq in enumerate(sample, 1):
        print(f"\n[{i}/{len(sample)}] {bq.ticker} [{bq.cat_a} + {bq.cat_b}]")
        for arm, ctx_fn in ARMS.items():
            ctx, coverage = ctx_fn(bq)
            ans = answer(aclient, aspec.model, bq.query, ctx)
            f1 = token_f1(ans, bq.gold)
            js = judge(jclient, jspec.model, bq.query, bq.gold, ans)
            agg[arm]["f1"].append(f1)
            agg[arm]["judge"].append(js)
            agg[arm]["cov"].append(coverage)
            cov_points.append(coverage)
            judge_points.append(js)
            print(f"   {arm:<18} coverage={coverage:.2f} f1={f1:.3f} judge={js:.2f}")
    print("\n" + "=" * 86)
    print(
        f"  {'arm':<18} {'mean coverage':>13} {'mean token_f1':>14} {'mean judge':>12}"
    )
    print("  " + "-" * 60)
    for arm in ARMS:
        a = agg[arm]
        print(
            f"  {arm:<18} {sum(a['cov'])/len(a['cov']):>13.2f} "
            f"{sum(a['f1'])/len(a['f1']):>14.3f} {sum(a['judge'])/len(a['judge']):>12.3f}"
        )
    rho = spearman(cov_points, judge_points)
    print(f"\n  Spearman rho(cross_category_coverage, judge_score) = {rho}")
    print("  Honest reading (full 17 B-questions vs the n=6 smoke):")
    print("  - rho fell 0.435 (smoke, homogeneous Fin+Overview) -> ~0.18 (full, with")
    print(
        "    Footnotes pairs): the coverage->judge bridge is WEAK-positive, not strong;"
    )
    print(
        "    the smoke overstated it. The 'isolated scores 0 on every B-question' from"
    )
    print(
        "    the smoke also breaks (some single-category answers land 0.5 on the full set)."
    )
    print(
        "  - token_f1 still ranks backbone_both > isolated; the LLM judge is NOISY at this"
    )
    print("    scale (backbone_xbrl tops judge but bottoms token_f1 — contradictory).")
    print(
        "  - Takeaway: the backbone's cross-category advantage holds DIRECTIONALLY, but the"
    )
    print(
        "    deterministic metric (PR #195/#196) is the reliable signal; the LLM judge is"
    )
    print(
        "    confirmatory and noisy. Caveat: synthetic B-questions, n=17, MARA variance."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
