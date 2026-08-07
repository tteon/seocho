#!/usr/bin/env python3
"""Zero-cost paired analysis for the LoG 2026 federation paper.

This script analyzes already completed hq-42k artifacts.  It deliberately
labels the output as preliminary because the historical provider/silo baseline
and the latest category lane were built from different extraction substrates.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(row: dict[str, Any], name: str = "token_f1") -> float:
    return float((row.get("evaluation") or {}).get(name) or 0.0)


def _rows_by_case(records: Iterable[dict[str, Any]], lane: str) -> dict[str, dict[str, Any]]:
    return {
        str(row["case_id"]): row
        for row in records
        if row.get("lane") == lane and row.get("case_id")
    }


def paired_bootstrap_ci(
    differences: list[float], *, samples: int = 10_000, seed: int = 42
) -> tuple[float, float]:
    """Return a percentile bootstrap CI for a paired mean difference."""
    if not differences:
        raise ValueError("differences must not be empty")
    if samples < 2:
        raise ValueError("samples must be at least 2")
    rng = random.Random(seed)
    size = len(differences)
    estimates = sorted(
        mean(differences[rng.randrange(size)] for _ in range(size))
        for _ in range(samples)
    )
    lower = estimates[int(0.025 * (samples - 1))]
    upper = estimates[int(0.975 * (samples - 1))]
    return round(lower, 4), round(upper, 4)


def compare_paired(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
    *,
    metric: str = "token_f1",
    bootstrap_samples: int = 10_000,
    seed: int = 42,
) -> dict[str, Any]:
    case_ids = sorted(set(candidate) & set(baseline))
    if not case_ids:
        raise ValueError("candidate and baseline have no shared case ids")
    differences = [_metric(candidate[cid], metric) - _metric(baseline[cid], metric) for cid in case_ids]
    epsilon = 1e-12
    wins = sum(delta > epsilon for delta in differences)
    losses = sum(delta < -epsilon for delta in differences)
    ties = len(differences) - wins - losses
    return {
        "n": len(case_ids),
        "candidate_mean": round(mean(_metric(candidate[cid], metric) for cid in case_ids), 4),
        "baseline_mean": round(mean(_metric(baseline[cid], metric) for cid in case_ids), 4),
        "mean_delta": round(mean(differences), 4),
        "bootstrap_95_ci": list(
            paired_bootstrap_ci(differences, samples=bootstrap_samples, seed=seed)
        ),
        "wins": wins,
        "ties": ties,
        "losses": losses,
    }


def _category_comparisons(
    candidate: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    categories: dict[str, list[str]] = defaultdict(list)
    for case_id in sorted(set(candidate) & set(baseline)):
        categories[str(candidate[case_id].get("category") or "unknown")].append(case_id)
    result: dict[str, Any] = {}
    for category, case_ids in sorted(categories.items()):
        deltas = [_metric(candidate[cid]) - _metric(baseline[cid]) for cid in case_ids]
        result[category] = {
            "n": len(case_ids),
            "candidate_mean": round(mean(_metric(candidate[cid]) for cid in case_ids), 4),
            "baseline_mean": round(mean(_metric(baseline[cid]) for cid in case_ids), 4),
            "mean_delta": round(mean(deltas), 4),
        }
    return result


def build_analysis(
    baseline: dict[str, Any],
    category: dict[str, Any],
    *,
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    baseline_records = baseline.get("records") or []
    category_records = category.get("records") or []
    candidate = _rows_by_case(category_records, "category-federation")
    broadcast = _rows_by_case(baseline_records, "federation")

    silo_lanes = {
        str(name): lane
        for name, lane in (baseline.get("lanes") or {}).items()
        if str(name).startswith("silo-")
    }
    if not silo_lanes:
        raise ValueError("baseline has no silo lanes")
    best_silo_lane = max(silo_lanes, key=lambda name: float(silo_lanes[name].get("token_f1") or 0.0))
    best_silo = _rows_by_case(baseline_records, best_silo_lane)

    return {
        "status": "preliminary_non_causal",
        "warning": (
            "Category and historical baseline lanes use different extraction substrates. "
            "Use these paired case statistics for planning, not causal federation claims."
        ),
        "best_fixed_silo_lane": best_silo_lane,
        "category_vs_broadcast": compare_paired(
            candidate, broadcast, bootstrap_samples=bootstrap_samples
        ),
        "category_vs_best_fixed_silo": compare_paired(
            candidate, best_silo, bootstrap_samples=bootstrap_samples
        ),
        "category_vs_broadcast_by_category": _category_comparisons(candidate, broadcast),
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# LoG 2026 Preliminary Paired Analysis",
        "",
        f"Status: `{payload['status']}`",
        "",
        f"> {payload['warning']}",
        "",
        "## Overall paired comparisons",
        "",
        "| Comparison | N | Candidate | Baseline | Mean delta | Bootstrap 95% CI | W/T/L |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    for label, key in (
        ("Category vs broadcast", "category_vs_broadcast"),
        ("Category vs best fixed silo", "category_vs_best_fixed_silo"),
    ):
        row = payload[key]
        lines.append(
            f"| {label} | {row['n']} | {row['candidate_mean']:.4f} | "
            f"{row['baseline_mean']:.4f} | {row['mean_delta']:+.4f} | "
            f"[{row['bootstrap_95_ci'][0]:+.4f}, {row['bootstrap_95_ci'][1]:+.4f}] | "
            f"{row['wins']}/{row['ties']}/{row['losses']} |"
        )
    lines.extend(
        [
            "",
            "## Category vs broadcast",
            "",
            "| Category | N | Candidate | Baseline | Mean delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for category, row in payload["category_vs_broadcast_by_category"].items():
        lines.append(
            f"| {category} | {row['n']} | {row['candidate_mean']:.4f} | "
            f"{row['baseline_mean']:.4f} | {row['mean_delta']:+.4f} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run-prefix", default="fedcat-baseline-80-v1")
    parser.add_argument("--category-run-prefix", default="fedcat-wide-lite-survivorship-v1")
    parser.add_argument("--output-run-prefix", default="log2026-paper-analysis")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args()

    base = ROOT / "outputs" / "evaluation" / "mdm_fedcat"
    payload = build_analysis(
        _load(base / args.baseline_run_prefix / "federation_aggregate.json"),
        _load(base / args.category_run_prefix / "category_federation_aggregate.json"),
        bootstrap_samples=args.bootstrap_samples,
    )
    out = base / args.output_run_prefix
    out.mkdir(parents=True, exist_ok=True)
    (out / "paired_analysis.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_markdown(out / "paired_analysis.md", payload)
    print(f"wrote {out.relative_to(ROOT)}/paired_analysis.json")
    print(f"wrote {out.relative_to(ROOT)}/paired_analysis.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

