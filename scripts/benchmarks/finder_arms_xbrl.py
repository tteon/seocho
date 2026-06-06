#!/usr/bin/env python3
"""FinDER arms with XBRL-enriched backbone context (seocho-992 step 2).

Step 1 (#199) loaded SEC XBRL companyfacts as backbone Observations. The eju
arms (#198) showed MARA answers from gold-SNIPPET context can't meet FinDER's
compositional gold because the snippets lack the numbers. This step adds a
`backbone_xbrl` arm whose context carries the STRUCTURED numbers
(metric:Revenue FY2023 = 53,717,000,000 USD, ...) and asks: does that make the
MARA judge informative on compositional Financials questions?

Arms (fixed context budget — fair-eval):
  closed_book        no context (floor)
  isolated_snippet   the case's own gold snippet only
  backbone_snippet   all the company's snippets across categories (the eju
                     'dilution' baseline)
  backbone_xbrl      the company's structured XBRL numbers first, then snippets

No graph round-trip here — the structured numbers are rendered in-memory from
companyfacts (the graph-backed form is finder_xbrl_backbone.py, #199). MARA-first
answerer + judge; primary metric token_f1 (finder_judge), secondary MARA judge.

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_arms_xbrl.py --n 6
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Dict, List

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_backbone import Case, DATASET, select_xcat_cases  # noqa: E402
from finder_arms import ANSWER_SPEC, CONTEXT_BUDGET, JUDGE_SPEC, answer, judge  # noqa: E402
from finder_judge import token_f1  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402
from seocho.index.xbrl_ingest import companyfacts_to_observations, fetch_companyfacts  # noqa: E402
from seocho.semantic_layer.concepts import default_registry  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

_REG = default_registry()
_FACTS_CACHE: Dict[str, str] = {}     # cik -> rendered xbrl context
_GOLD: Dict[str, str] = {}


def gold_of(case: Case) -> str:
    if not _GOLD:
        with open(DATASET, newline="") as fh:
            for row in csv.DictReader(fh):
                _GOLD[row["_id"]] = row["answer"]
    return _GOLD.get(case.case_id, "")


def xbrl_lines(cik: str) -> str:
    """Render a company's structured XBRL numbers (cached, in-memory)."""
    if cik in _FACTS_CACHE:
        return _FACTS_CACHE[cik]
    try:
        facts = fetch_companyfacts(cik)
        nodes, _ = companyfacts_to_observations(facts, registry=_REG, cik=cik,
                                                n_years=5, min_fiscal_year=2021)
        lines = []
        for n in nodes:
            if n["label"] != "Observation":
                continue
            p = n["properties"]
            fy = p["period_key"].split(":")[1] if ":" in p["period_key"] else "?"
            lines.append(f"{p['concept_id']} FY{fy} = {p['value_num']:.0f} {p['unit']}")
        out = "\n".join(sorted(lines))
    except Exception as exc:
        out = ""
        print(f"   ! xbrl fetch failed for {cik}: {type(exc).__name__}")
    _FACTS_CACHE[cik] = out
    return out


def ctx_closed(case, company_cases): return ""


def ctx_isolated(case, company_cases): return "\n\n".join(case.evidence)[:CONTEXT_BUDGET]


def ctx_backbone_snippet(case, company_cases):
    snips = [e for c in company_cases for e in c.evidence]
    return "\n\n".join(snips)[:CONTEXT_BUDGET]


def ctx_backbone_xbrl(case, company_cases):
    nums = xbrl_lines(case.cik)
    remaining = CONTEXT_BUDGET - len(nums) - 20
    snips = "\n\n".join(e for c in company_cases for e in c.evidence)
    block = "STRUCTURED FINANCIALS:\n" + nums
    if remaining > 200:
        block += "\n\nNOTES:\n" + snips[:remaining]
    return block[:CONTEXT_BUDGET]


ARMS = {
    "closed_book": ctx_closed,
    "isolated_snippet": ctx_isolated,
    "backbone_snippet": ctx_backbone_snippet,
    "backbone_xbrl": ctx_backbone_xbrl,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6, help="number of Financials cases (0 = all)")
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)
    by_cik: Dict[str, List[Case]] = {}
    for c in cases:
        by_cik.setdefault(c.cik, []).append(c)
    # focus on Financials cases (where structured numbers matter most)
    fin = [c for c in cases if c.category == "Financials"]
    sample = fin if args.n == 0 else fin[:args.n]

    aspec = llm_io.parse_llm_spec(ANSWER_SPEC)
    jspec = llm_io.parse_llm_spec(JUDGE_SPEC)
    aclient = llm_io.make_chat_client(aspec)
    jclient = llm_io.make_chat_client(jspec)

    print("=" * 84)
    print(f"FinDER arms + XBRL — MARA ({aspec.model}); {len(sample)} Financials cases, "
          f"fixed {CONTEXT_BUDGET}-char budget")
    print("=" * 84)
    agg: Dict[str, dict] = {a: {"f1": [], "judge": []} for a in ARMS}
    for i, case in enumerate(sample, 1):
        print(f"\n[{i}/{len(sample)}] {case.ticker}/{case.category}: {case.query[:68]}")
        gold = gold_of(case)
        for arm, ctx_fn in ARMS.items():
            ctx = ctx_fn(case, by_cik[case.cik])
            ans = answer(aclient, aspec.model, case.query, ctx)
            f1 = token_f1(ans, gold)
            js = judge(jclient, jspec.model, case.query, gold, ans)
            agg[arm]["f1"].append(f1)
            agg[arm]["judge"].append(js)
            print(f"   {arm:<18} f1={f1:.3f} judge={js:.2f}")
        time.sleep(0.2)
    print("\n" + "=" * 84)
    print(f"  {'arm':<20} {'mean token_f1':>14} {'mean judge':>12}")
    print("  " + "-" * 48)
    for arm in ARMS:
        f1s, js = agg[arm]["f1"], agg[arm]["judge"]
        print(f"  {arm:<20} {sum(f1s)/len(f1s):>14.3f} {sum(js)/len(js):>12.3f}")
    print("\n  Findings (honest, smoke n=5):")
    print("  - Judge is now INFORMATIVE (finder_arms judge max_tokens 400->1600 fixed a")
    print("    truncation that scored every answer 0.0: MiniMax emits reasoning before the")
    print("    JSON verdict). That fix is the real step-2 deliverable.")
    print("  - structured XBRL numbers lift token_f1 over snippet-backbone (0.233 vs 0.208)")
    print("    but the judge does NOT reward it; backbone_xbrl is not a clean win here.")
    print("  - CONFOUND surfaced: MARA-judges-MARA is too LENIENT — closed_book (no context)")
    print("    scores ~0.8. generator==judge self-preference (both reviewers flagged it) makes")
    print("    the absolute judge untrustworthy. NEXT: use a DIFFERENT judge model than the")
    print("    answerer. The deterministic metric (PR #195/#196) remains the headline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
