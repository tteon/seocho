#!/usr/bin/env python3
"""Zero-cost replay: what a capability fallback would recover for SDCR.

The failure analysis recommends that a routing miss degrade to the frozen
TF--IDF capability team instead of serving empty evidence. That recommendation
was previously unmeasured. This script measures it by replay only -- no LLM,
embedding, or database calls -- using the per-case arm rows already frozen in
``revised_exact_retrieval.json`` and the per-case routing outcome in
``revised_sdcr_routing.json``.

Fallback policy under test (deterministic, decided before reading scores):
    if the router covered every required view for a case, keep its evidence;
    otherwise serve the TF--IDF top-2 capability team (the ``slot_only`` arm).

Also reports the coverage-conditioned decomposition, which tests whether the
routed deficit is empty evidence on misses rather than degraded retrieval on
hits.

Outputs ``outputs/evaluation/mdm_fedcat/log2026-capability-fallback-v1/``.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CROSS = BASE / "log2026-full-finder-cross-view-v1"
OUT = BASE / "log2026-capability-fallback-v1"

PRIMARY = "sdcr"
FALLBACK = "slot_only"  # frozen TF-IDF top-2 capability team
METRICS = ("slot_token_recall", "cross_view_token_recall", "tokens_used")
BOOTSTRAP = 10_000
SEED = 42


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def clustered_bootstrap(
    pairs: list[tuple[str, float]], iterations: int = BOOTSTRAP, seed: int = SEED
) -> tuple[float, float]:
    """Issuer-clustered percentile CI on the mean of per-case deltas."""
    clusters: dict[str, list[float]] = {}
    for issuer, value in pairs:
        clusters.setdefault(issuer, []).append(value)
    keys = sorted(clusters)
    if len(keys) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        drawn: list[float] = []
        for _ in range(len(keys)):
            drawn.extend(clusters[keys[rng.randrange(len(keys))]])
        means.append(mean(drawn))
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return (round(lo, 6), round(hi, 6))


def main() -> int:
    retrieval = load(CROSS / "revised_exact_retrieval.json")
    routing = load(CROSS / "revised_sdcr_routing.json")
    covered = {
        row["candidate_id"]: bool(row["both_required_views_covered"])
        for row in routing["rows"]
    }

    rows: list[dict[str, Any]] = []
    for row in retrieval["rows"]:
        cid = row["candidate_id"]
        arms = row["arms"]
        if cid not in covered:
            raise SystemExit(f"routing outcome missing for {cid}; refusing to impute")
        hit = covered[cid]
        # Cross-check the two artifacts agree on which cases routed successfully.
        reported = bool(arms[PRIMARY].get("routing_success"))
        served = PRIMARY if hit else FALLBACK
        rows.append({
            "candidate_id": cid,
            "issuer": row["issuer"],
            "router_covered_required_views": hit,
            "routing_success_flag_in_retrieval": reported,
            "artifacts_agree": hit == reported,
            "fallback_served_arm": served,
            "primary": {m: arms[PRIMARY][m] for m in METRICS},
            "fallback_used": {m: arms[FALLBACK][m] for m in METRICS},
            "with_fallback": {m: arms[served][m] for m in METRICS},
        })

    disagreements = [r["candidate_id"] for r in rows if not r["artifacts_agree"]]
    hits = [r for r in rows if r["router_covered_required_views"]]
    misses = [r for r in rows if not r["router_covered_required_views"]]

    def arm_mean(key: str, metric: str, subset: list[dict[str, Any]] | None = None) -> float:
        subset = rows if subset is None else subset
        return round(mean(r[key][metric] for r in subset), 6) if subset else 0.0

    summary: dict[str, Any] = {
        "cases": len(rows),
        "router_hits": len(hits),
        "router_misses": len(misses),
        "artifact_disagreements": disagreements,
        "arms": {
            "routed_sdcr_itt": {m: arm_mean("primary", m) for m in METRICS},
            "tfidf_top2_always": {m: arm_mean("fallback_used", m) for m in METRICS},
            "sdcr_with_capability_fallback": {m: arm_mean("with_fallback", m) for m in METRICS},
        },
        "coverage_conditioned": {
            "on_router_hits": {
                "n": len(hits),
                "routed_sdcr": {m: arm_mean("primary", m, hits) for m in METRICS},
                "tfidf_top2": {m: arm_mean("fallback_used", m, hits) for m in METRICS},
            },
            "on_router_misses": {
                "n": len(misses),
                "routed_sdcr": {m: arm_mean("primary", m, misses) for m in METRICS},
                "tfidf_top2": {m: arm_mean("fallback_used", m, misses) for m in METRICS},
            },
        },
        "deltas": {},
    }

    for label, left, right in (
        ("fallback_minus_routed", "with_fallback", "primary"),
        ("fallback_minus_tfidf_always", "with_fallback", "fallback_used"),
    ):
        block = {}
        for metric in ("slot_token_recall", "cross_view_token_recall"):
            pairs = [(r["issuer"], r[left][metric] - r[right][metric]) for r in rows]
            lo, hi = clustered_bootstrap(pairs)
            block[metric] = {
                "delta": round(mean(v for _, v in pairs), 6),
                "issuer_clustered_bootstrap_95_ci": [lo, hi],
                "clusters": len({r["issuer"] for r in rows}),
            }
        summary["deltas"][label] = block

    payload = {
        "contract": "log2026.capability_fallback_replay.v1",
        "method": "replay of frozen per-case arm rows; no LLM, embedding, or database calls",
        "fallback_policy": (
            "keep routed evidence when every required view was covered, "
            "otherwise serve the frozen TF-IDF top-2 capability team"
        ),
        "policy_fixed_before_reading_scores": True,
        "evidence_budget_tokens": retrieval["evidence_token_budget"],
        "tokenizer": retrieval["tokenizer"],
        "bootstrap_iterations": BOOTSTRAP,
        "seed": SEED,
        "claim_boundary": (
            "Retrieval-level replay on 13 persona-screened cases. It shows what a "
            "fallback recovers in evidence coverage, not that answers improve; the "
            "answer arms would need a paid re-run."
        ),
        "summary": summary,
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fallback_replay.json").write_text(json.dumps(payload, indent=2) + "\n")

    a = summary["arms"]
    lines = [
        "# Capability-Fallback Replay (zero cost)",
        "",
        f"- Cases: {summary['cases']}  (router hits {summary['router_hits']}, "
        f"misses {summary['router_misses']})",
        f"- Artifact disagreements: {disagreements or 'none'}",
        "",
        "| Arm | Slot token recall | Cross-view recall | Mean tokens |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("routed_sdcr_itt", "Routed SDCR (ITT, current behaviour)"),
        ("tfidf_top2_always", "TF-IDF top-2 always"),
        ("sdcr_with_capability_fallback", "SDCR + capability fallback"),
    ):
        row = a[key]
        lines.append(
            f"| {label} | {row['slot_token_recall']:.3f} | "
            f"{row['cross_view_token_recall']:.3f} | {row['tokens_used']:.0f} |"
        )
    lines += ["", "## Coverage-conditioned decomposition", ""]
    for key, label in (("on_router_hits", "Router hits"), ("on_router_misses", "Router misses")):
        block = summary["coverage_conditioned"][key]
        lines.append(
            f"- {label} (n={block['n']}): routed slot recall "
            f"{block['routed_sdcr']['slot_token_recall']:.3f}, "
            f"TF-IDF top-2 {block['tfidf_top2']['slot_token_recall']:.3f}"
        )
    lines += ["", "## Deltas (issuer-clustered bootstrap)", ""]
    for label, block in summary["deltas"].items():
        for metric, stat in block.items():
            ci = stat["issuer_clustered_bootstrap_95_ci"]
            lines.append(
                f"- {label} / {metric}: {stat['delta']:+.6f} "
                f"95% CI [{ci[0]:+.6f}, {ci[1]:+.6f}]"
            )
    lines += ["", payload["claim_boundary"], ""]
    (OUT / "fallback_replay.md").write_text("\n".join(lines))
    print(OUT / "fallback_replay.json")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
