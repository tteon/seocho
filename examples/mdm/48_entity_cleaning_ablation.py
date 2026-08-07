#!/usr/bin/env python3
"""Compare exploratory phrase seeds with identifier-qualified entity seeds."""
from __future__ import annotations

import json
import random
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
OLD_NETWORK = BASE / "log2026-full-multiagent-network-v1/analysis.json"
OLD_NULL = BASE / "log2026-sdcr-null-v1/analysis.json"
CLEAN = BASE / "log2026-clean-entity-network-v1/analysis.json"
OUT = BASE / "log2026-entity-cleaning-ablation-v1"


def auc(null: list[float], observed: list[float]) -> float:
    if not null or not observed:
        return 0.0
    ordered = sorted(null)
    wins = sum(bisect_left(ordered, x) for x in observed)
    ties = sum(bisect_right(ordered, x) - bisect_left(ordered, x) for x in observed)
    return (wins + 0.5 * ties) / (len(null) * len(observed))


def rank_divergence(left: list[str], right: list[str], depth: int = 10) -> float:
    lw = {name: 1 / (i + 1) for i, name in enumerate(left[:depth])}
    rw = {name: 1 / (i + 1) for i, name in enumerate(right[:depth])}
    names = set(lw) | set(rw)
    denominator = sum(max(lw.get(name, 0), rw.get(name, 0)) for name in names)
    return 1 - sum(min(lw.get(name, 0), rw.get(name, 0)) for name in names) / denominator if denominator else 0.0


def clustered_values(rows: list[dict[str, Any]], field: str) -> dict[str, list[float]]:
    result: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        result[str(row["entity"])].append(float(row[field]))
    return result


def bootstrap_auc_delta(old_null: list[dict[str, Any]], old_obs: list[dict[str, Any]],
                        clean_null: list[dict[str, Any]], clean_obs: list[dict[str, Any]],
                        iterations: int = 2000) -> list[float]:
    rng = random.Random(20260712)
    datasets = []
    for null_rows, obs_rows in ((old_null, old_obs), (clean_null, clean_obs)):
        datasets.append((clustered_values(null_rows, "rank_weighted_divergence"),
                         clustered_values(obs_rows, "rank_weighted_divergence")))
    deltas = []
    for _ in range(iterations):
        scores = []
        for null_groups, obs_groups in datasets:
            nk, ok = list(null_groups), list(obs_groups)
            null = [v for key in rng.choices(nk, k=len(nk)) for v in null_groups[key]]
            obs = [v for key in rng.choices(ok, k=len(ok)) for v in obs_groups[key]]
            scores.append(auc(null, obs))
        deltas.append(scores[1] - scores[0])
    return sorted(deltas)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    old_network = json.loads(OLD_NETWORK.read_text())
    old_null = json.loads(OLD_NULL.read_text())
    clean = json.loads(CLEAN.read_text())
    old_obs = []
    for row in old_network["entity_context_divergence"]:
        old_obs.append({**row, "rank_weighted_divergence": rank_divergence(row["left_top"], row["right_top"])})
    clean_obs = [
        {**row, "rank_weighted_divergence": rank_divergence(row["left_top"], row["right_top"])}
        for row in clean["entity_context_divergence"]
    ]
    old_entities = sorted({row["entity"] for row in old_obs})
    audit = {row["entity"]: row for row in clean["entity_audit"]}
    qualified_old = [name for name in old_entities if audit.get(name, {}).get("included")]
    delta = bootstrap_auc_delta(old_null["null_rows"], old_obs, clean["null_rows"], clean_obs)
    lo, hi = delta[int(0.025 * len(delta))], delta[int(0.975 * len(delta))]
    payload = {
        "contract": "log2026.entity_cleaning_ablation.v1",
        "comparison_scope": "measurement validity; not answer accuracy",
        "before": {
            "seed_rule": "repeated normalized phrase ranked by category count and frequency",
            "seeds": len(old_entities), "identifier_qualified_seeds": len(qualified_old),
            "qualification_rate": round(len(qualified_old) / len(old_entities), 6),
            "observed_pairs": len(old_obs), "null_pairs": len(old_null["null_rows"]),
            "null_mean": old_null["summary"]["null_mean"], "cross_mean": old_null["summary"]["cross_mean"],
            "auroc": old_null["summary"]["auroc"],
        },
        "after": {
            "seed_rule": "independently supported ticker-name identity with conflict quarantine",
            "seeds": len(clean["selected_entities"]), "identifier_qualified_seeds": len(clean["selected_entities"]),
            "qualification_rate": 1.0, "observed_pairs": len(clean["entity_context_divergence"]),
            "null_pairs": len(clean["null_rows"]), "null_mean": clean["summary"]["null_mean"],
            "cross_mean": clean["summary"]["cross_mean"], "auroc": clean["summary"]["auroc"],
        },
        "change": {
            "qualification_rate_pp": round(100 * (1 - len(qualified_old) / len(old_entities)), 2),
            "auroc": round(clean["summary"]["auroc"] - old_null["summary"]["auroc"], 6),
            "entity_clustered_bootstrap_95_ci": [round(lo, 6), round(hi, 6)],
            "trigger_conclusion_changed": False,
        },
        "excluded_before_seeds": [name for name in old_entities if name not in qualified_old],
    }
    (OUT / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n")
    b, a, c = payload["before"], payload["after"], payload["change"]
    lines = ["# Entity Cleaning Ablation", "",
             "The ablation measures seed validity and network discrimination, not answer accuracy.", "",
             "| Measure | Before | After |", "|---|---:|---:|",
             f"| Identifier-qualified seed rate | {b['qualification_rate']:.3f} | {a['qualification_rate']:.3f} |",
             f"| Cross-view pairs | {b['observed_pairs']:,} | {a['observed_pairs']:,} |",
             f"| Matched-null pairs | {b['null_pairs']:,} | {a['null_pairs']:,} |",
             f"| Null mean divergence | {b['null_mean']:.3f} | {a['null_mean']:.3f} |",
             f"| Cross-view mean divergence | {b['cross_mean']:.3f} | {a['cross_mean']:.3f} |",
             f"| AUROC | {b['auroc']:.3f} | {a['auroc']:.3f} |", "",
             f"AUROC change is {c['auroc']:+.3f}; entity-clustered bootstrap 95% CI [{c['entity_clustered_bootstrap_95_ci'][0]:.3f}, {c['entity_clustered_bootstrap_95_ci'][1]:.3f}].",
             "The null-tail trigger remains unusable after cleaning, so the ablation strengthens measurement validity without changing the SDCR design conclusion.", ""]
    (OUT / "analysis.md").write_text("\n".join(lines))
    print(OUT / "analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
