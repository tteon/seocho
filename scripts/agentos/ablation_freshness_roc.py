"""The central OS-necessity experiment: freshness-conditioned strict beats both
always-block and always-warn on the refusal ROC (seocho-ia4.6).

Thesis (hadry): using the ontology as a guardrail, "strict" is only safe if
recency AND relevance are judged well — otherwise strict-but-stale enforces a dead
contract. This ablation proves it against the two fixed corners.

Setup (deterministic, no RNG, no LLM): a population of reads issued after an
ontology version bump. Each read has:
- ``relevant``: does the bump touch the labels this read queries?
- ``distance``: how many versions the data lags the active contract.
A true ``reconcilable_horizon`` H is a property of the data: a *relevant* drift
within H versions is reconcilable on read (served answer is CORRECT after repair);
a relevant drift beyond H is genuinely breaking (served answer is WRONG). An
*irrelevant* drift never affects the answer (CORRECT if served).

  correct_if_served(read) = (not relevant) or (distance <= H)

Policies decide serve vs refuse:
- ALWAYS_WARN  : serve everything (today's default before ia4.1).
- ALWAYS_BLOCK : refuse every version mismatch (unconditional strict = ia4.1 block).
- FRESHNESS(b) : the bounded-staleness policy (evaluate_freshness), refuse iff the
                 drift is relevant AND distance > b.

Metrics:
- under_refusal = served-but-WRONG / harmful      (answering a stale-invalid query)
- over_refusal  = refused-but-CORRECT / correct   (refusing a still-valid query)

Headline: ALWAYS_WARN has 0 over / max under; ALWAYS_BLOCK has max over / 0 under;
FRESHNESS at a well-chosen bound (b == H) achieves ~0 on BOTH — it dominates the
two corners. A bound sweep traces the ROC frontier.

Usage: python scripts/agentos/ablation_freshness_roc.py --out outputs/agentos/freshness_roc.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.ontology.freshness import FreshnessSignals, evaluate_freshness  # noqa: E402

_MAX_DISTANCE = 5          # versions behind, 1..5
_RECONCILABLE_HORIZON = 2  # relevant drift within 2 versions is reconcilable on read
_COPIES = 30               # reads per (relevant, distance) cell -> stable rates


def _population() -> List[Dict[str, Any]]:
    """A balanced population over (relevant, distance)."""
    pop = []
    for relevant in (False, True):
        for distance in range(1, _MAX_DISTANCE + 1):
            for _ in range(_COPIES):
                correct_if_served = (not relevant) or (distance <= _RECONCILABLE_HORIZON)
                pop.append({
                    "relevant": relevant,
                    "distance": distance,
                    "correct_if_served": correct_if_served,
                })
    return pop


def _decide(policy: str, read: Dict[str, Any], *, bound: int) -> bool:
    """Return True if the policy SERVES the read (False = refuse)."""
    if policy == "always_warn":
        return True
    if policy == "always_block":
        return False   # every read here has a version mismatch
    # freshness-conditioned (the real module)
    sig = FreshnessSignals(
        version_mismatch=True,
        version_distance=read["distance"],
        drift_relevance=1.0 if read["relevant"] else 0.0,
    )
    dec = evaluate_freshness(sig, max_version_distance=bound)
    return not dec.blocks


def _score(pop, policy, *, bound: int) -> Dict[str, float]:
    harmful = [r for r in pop if not r["correct_if_served"]]
    correct = [r for r in pop if r["correct_if_served"]]
    served_wrong = sum(1 for r in harmful if _decide(policy, r, bound=bound))
    refused_correct = sum(1 for r in correct if not _decide(policy, r, bound=bound))
    return {
        "under_refusal": round(served_wrong / max(len(harmful), 1), 3),
        "over_refusal": round(refused_correct / max(len(correct), 1), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pop = _population()
    report: Dict[str, Any] = {
        "population": len(pop),
        "max_distance": _MAX_DISTANCE,
        "reconcilable_horizon": _RECONCILABLE_HORIZON,
        "fixed_policies": {},
        "freshness_bound_sweep": {},
    }

    for pol in ("always_warn", "always_block"):
        report["fixed_policies"][pol] = _score(pop, pol, bound=0)
    # freshness at the matched bound (b == H) is the headline point
    report["fixed_policies"]["freshness_b=H"] = _score(pop, "freshness", bound=_RECONCILABLE_HORIZON)

    best = None
    for b in range(0, _MAX_DISTANCE + 1):
        s = _score(pop, "freshness", bound=b)
        report["freshness_bound_sweep"][b] = s
        cost = s["under_refusal"] + s["over_refusal"]
        if best is None or cost < best[1]:
            best = (b, cost, s)
    report["best_bound"] = {"bound": best[0], "under_refusal": best[2]["under_refusal"],
                            "over_refusal": best[2]["over_refusal"]}

    fw = report["fixed_policies"]["always_warn"]
    fb = report["fixed_policies"]["always_block"]
    ff = report["fixed_policies"]["freshness_b=H"]
    print("=== freshness refusal-ROC ablation (seocho-ia4.6) ===")
    print(f"  population={len(pop)}  reconcilable_horizon H={_RECONCILABLE_HORIZON}")
    print(f"  {'policy':22s} {'under_refusal':>14s} {'over_refusal':>13s}")
    print(f"  {'always_warn':22s} {fw['under_refusal']:>14.0%} {fw['over_refusal']:>13.0%}")
    print(f"  {'always_block':22s} {fb['under_refusal']:>14.0%} {fb['over_refusal']:>13.0%}")
    print(f"  {'freshness (b=H)':22s} {ff['under_refusal']:>14.0%} {ff['over_refusal']:>13.0%}  <- dominates both corners")
    print(f"  best bound b={report['best_bound']['bound']} "
          f"(under={report['best_bound']['under_refusal']:.0%}, over={report['best_bound']['over_refusal']:.0%})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
