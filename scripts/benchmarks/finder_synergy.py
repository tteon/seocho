#!/usr/bin/env python3
"""FinDER synergy benchmark — the composed headline metric (council seocho-9xo).

The luminary council's chosen headline (synergy #2): on FinDER, signal-routed
model selection costs **< 0.6x all-frontier** while answer-support is held within
noise of the all-frontier baseline. This composes two SEOCHO differentiators —
**ontology-governed answering** (support/faithfulness) and the **agent router**
("route on a known signal" → model tier) — into one number.

The router is not yet wired into the live query path (verified: model_router has
zero live importers; that wiring is ticket seocho-jdg). So this harness composes
the arms EXTERNALLY and is honest about it:

  * cost arm (deterministic, runs today): route each REAL FinDER case by its
    known signal (category/reasoning_type) → tier → relative cost; report
    routed_cost / all_frontier_cost on the real category distribution.
  * support arm (--live, MARA): answer a sample under all-frontier vs routed
    model and compare answer-support/match parity. Proves the cost saving does
    not cost answer quality. (Heavier; the full run lands once seocho-jdg wires
    the router into graph_cot_flow.)

Run:
  scripts/benchmarks/finder_synergy.py --dataset examples/finder/datasets/finder_tutorial_subset.json
  scripts/benchmarks/finder_synergy.py --live 3   # + MARA support-parity sample
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src", ROOT / "extraction"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from seocho.routing import ModelRouter, ModelTier  # noqa: E402
from seocho.benchmarking import (  # noqa: E402
    FinDERBenchmarkCase,
    compare_answers,
    load_finder_cases,
    score_answer_slots,
)

# --------------------------------------------------------------------------- #
# Signal → tier: "route on a known signal you already have" (the FinDER category
# + reasoning_type IS the signal). Hard multi-step financial math needs the
# frontier; single-passage lookups do not.
# --------------------------------------------------------------------------- #

_HARD_REASONING = {"compositional", "subtraction", "numeric"}
_BALANCED_CATEGORIES = {"footnotes", "accounting", "legal", "risk", "governance"}
_FAST_CATEGORIES = {"companyoverview", "company_overview", "shareholderreturn"}


def route_tier_for_case(case: FinDERBenchmarkCase) -> ModelTier:
    category = str(getattr(case, "category", "") or "").strip().lower()
    reasoning = str(getattr(case, "reasoning_type", "") or "").strip().lower()
    if reasoning in _HARD_REASONING:
        return ModelTier.FRONTIER
    if category in _FAST_CATEGORIES:
        return ModelTier.FAST
    if category == "financials":
        return ModelTier.FRONTIER  # numbers with units/periods — don't risk a cheap model
    if category in _BALANCED_CATEGORIES:
        return ModelTier.BALANCED
    return ModelTier.BALANCED


def synergy_cost_report(cases: List[FinDERBenchmarkCase], router: ModelRouter) -> Dict[str, Any]:
    """Deterministic: routed vs all-frontier relative cost on the real FinDER mix."""
    routed = 0.0
    frontier = 0.0
    tier_counts: Dict[str, int] = {}
    for case in cases:
        tier = route_tier_for_case(case)
        tier_counts[tier.name] = tier_counts.get(tier.name, 0) + 1
        routed += router.relative_cost(tier)
        frontier += router.relative_cost(ModelTier.FRONTIER)
    ratio = (routed / frontier) if frontier else 1.0
    return {
        "n": len(cases),
        "tier_counts": tier_counts,
        "routed_cost": routed,
        "all_frontier_cost": frontier,
        "cost_ratio": ratio,
        "cost_saving_pct": round((1.0 - ratio) * 100, 1),
        "meets_0_6x_target": ratio < 0.6,
    }


# --------------------------------------------------------------------------- #
# Live support-parity arm (optional, MARA)
# --------------------------------------------------------------------------- #

def _mara_key() -> Optional[str]:
    key = os.getenv("MARA_API_KEY")
    if key:
        return key
    try:
        for line in open(ROOT / ".env", encoding="utf-8"):
            m = re.match(r'\s*MARA_API_KEY\s*=\s*"?([^"\n]+)"?', line)
            if m:
                return m.group(1).strip()
    except OSError:
        pass
    return None


def _ontology():
    from seocho import NodeDef, Ontology, P, RelDef
    return Ontology(
        name="finder_synergy",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "year": P(str)}),
            "Risk": NodeDef(properties={"name": P(str, unique=True), "category": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            "FACES": RelDef(source="Company", target="Risk"),
        },
    )


def run_live_support_arm(cases: List[FinDERBenchmarkCase], router: ModelRouter, key: str) -> Dict[str, Any]:
    """Answer a sample under all-frontier vs routed model; compare support/match parity.

    Ontology-governed answering is held fixed; only the answer model varies, so
    the delta isolates 'does cheap-when-signal-allows cost answer quality?'.
    """
    from seocho import Seocho

    frontier_model = router.tier_models[ModelTier.FRONTIER]

    def _score(model: str, case: FinDERBenchmarkCase) -> Dict[str, Any]:
        client = Seocho.local(_ontology(), llm=f"mara/{model}", api_key=key,
                              workspace_id=f"synergy-{model}")
        try:
            client.add(case.text, category=str(case.category or "memory"))
            ans = client.ask(case.question)
        finally:
            client.close()
        exact, contains = compare_answers(case.expected_answer, ans)
        slots = score_answer_slots(case.expected_answer, ans)
        return {"contains": contains, "numeric_recall": slots.get("numeric_recall", 0.0)}

    arms: Dict[str, Dict[str, float]] = {"all_frontier": {}, "routed": {}}
    rows = []
    for case in cases:
        tier = route_tier_for_case(case)
        routed_model = router.tier_models[tier]
        f = _score(frontier_model, case)
        r = _score(routed_model, case)
        rows.append({"case": case.case_id, "tier": tier.name, "routed_model": routed_model,
                     "frontier": f, "routed": r})
    def _agg(keyarm):
        vals = [row[keyarm] for row in rows]
        return {
            "contains_rate": round(sum(v["contains"] for v in vals) / len(vals), 3) if vals else 0.0,
            "numeric_recall": round(sum(v["numeric_recall"] for v in vals) / len(vals), 3) if vals else 0.0,
        }
    return {"n": len(rows), "all_frontier": _agg("frontier"), "routed": _agg("routed"), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="examples/finder/datasets/finder_tutorial_subset.json")
    ap.add_argument("--live", type=int, default=0, help="answer N sampled cases live via MARA for support parity")
    ap.add_argument("--out", default="outputs/evaluation/finder_synergy")
    args = ap.parse_args()

    cases = load_finder_cases(args.dataset)
    router = ModelRouter.mara_default()

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "tier_models": {t.name: m for t, m in router.tier_models.items()},
        "cost_arm": synergy_cost_report(cases, router),
    }

    if args.live > 0:
        key = _mara_key()
        if key:
            report["support_arm"] = run_live_support_arm(cases[: args.live], router, key)
        else:
            report["support_arm"] = {"skipped": "MARA_API_KEY unavailable"}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"synergy_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2))

    c = report["cost_arm"]
    print("=" * 64)
    print("FinDER SYNERGY — ontology-governed answering + signal-routed model")
    print("=" * 64)
    print(f"  cases={c['n']}  tier mix={c['tier_counts']}")
    print(f"  routed cost {c['routed_cost']:.0f} vs all-frontier {c['all_frontier_cost']:.0f}")
    print(f"  -> cost {c['cost_ratio']:.2f}x  (saving {c['cost_saving_pct']}%)  "
          f"{'MEETS' if c['meets_0_6x_target'] else 'below'} the <0.6x target")
    if report.get("support_arm", {}).get("rows"):
        s = report["support_arm"]
        print(f"  support parity (n={s['n']}): all-frontier contains={s['all_frontier']['contains_rate']} "
              f"vs routed contains={s['routed']['contains_rate']}")
    print(f"  written: {out_path}")


if __name__ == "__main__":
    main()
