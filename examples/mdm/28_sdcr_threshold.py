#!/usr/bin/env python3
"""Deterministic, label-free SDCR threshold utilities.

The null sample must come from matched same-case, same-category, same-profile
graphs that differ only by generation model. No answer labels are consumed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def null_tail_pvalue(observed: float, null_values: list[float]) -> float:
    if not null_values:
        raise ValueError("matched cross-model null values are required")
    return (1 + sum(value >= observed for value in null_values)) / (len(null_values) + 1)


def holm_rejections(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    ordered = sorted(enumerate(pvalues), key=lambda item: (item[1], item[0]))
    rejected = [False] * len(pvalues)
    for rank, (index, value) in enumerate(ordered):
        if value <= alpha / (len(pvalues) - rank):
            rejected[index] = True
        else:
            break
    return rejected


def trigger(
    *, best_single_slot_coverage: float, verify_required: bool,
    divergence_pvalues: list[float], alpha: float = 0.05,
) -> dict[str, Any]:
    corrected = holm_rejections(divergence_pvalues, alpha) if divergence_pvalues else []
    slot_trigger = best_single_slot_coverage < 1.0
    verification_trigger = verify_required and any(corrected)
    return {
        "multi_agent": slot_trigger or verification_trigger,
        "slot_trigger": slot_trigger,
        "verification_trigger": verification_trigger,
        "holm_rejections": corrected,
        "alpha": alpha,
    }


def marginal_agent_value(
    *, delta_slot: float, null_pvalue: float, verify_required: bool,
    token_cost: float, latency_cost: float, governance_risk: float,
    beta: float = 0.1, lambda_token: float = 1.0,
    lambda_latency: float = 1.0, lambda_governance: float = 1.0,
) -> float:
    verification = beta * (-math.log(max(null_pvalue, 1e-12))) if verify_required else 0.0
    return delta_slot + verification - lambda_token * token_cost - lambda_latency * latency_cost - lambda_governance * governance_risk


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--null", type=Path, required=True, help="JSON list of matched cross-model divergence values")
    parser.add_argument("--observed", type=float, required=True)
    args = parser.parse_args()
    null_values = json.loads(args.null.read_text())
    print(json.dumps({"observed": args.observed, "null_tail_pvalue": null_tail_pvalue(args.observed, null_values)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
