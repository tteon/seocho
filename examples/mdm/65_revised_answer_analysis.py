#!/usr/bin/env python3
"""Issuer-clustered statistics for revised multi-model answer outputs."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_answer_analysis.json"


def valid_score(row, field): return 0.0 if row["response"].get("parse_error") else float(row[field])


def bootstrap(rows, left: str, right: str, field: str, draws: int = 10000):
    by = defaultdict(dict)
    for row in rows: by[row["issuer"]][row["arm"]] = row
    issuers = sorted(issuer for issuer, arms in by.items() if left in arms and right in arms); rng = random.Random(20260712); values = []
    for _ in range(draws):
        sample = rng.choices(issuers, k=len(issuers)); values.append(mean(valid_score(by[i][right], field) - valid_score(by[i][left], field) for i in sample))
    point = mean(valid_score(by[i][right], field) - valid_score(by[i][left], field) for i in issuers); values.sort()
    return {"delta": round(point, 6), "issuer_clustered_bootstrap_95_ci": [round(values[250], 6), round(values[9750], 6)], "clusters": len(issuers)}


def main() -> int:
    payload = json.loads((BASE / "revised_answers.json").read_text()); rows = payload["rows"]; results = {}
    for model in payload["model_arms"]:
        selected = [row for row in rows if row["model"] == model]; results[model] = {}
        for field in ("slot_macro_f1", "numeric_recall", "cross_view_f1"):
            if {"centralized_single", "qualified_view_broadcast"} <= set(payload["model_arms"][model]):
                results[model]["broadcast_minus_centralized_" + field] = bootstrap(selected, "centralized_single", "qualified_view_broadcast", field)
            results[model]["sdcr_minus_centralized_" + field] = bootstrap(selected, "centralized_single", "sdcr", field)
        routed = [row for row in selected if row["arm"] == "sdcr" and not row["response"].get("routing_failure")]
        results[model]["sdcr_route_success_secondary"] = {field: round(mean(valid_score(row, field) for row in routed), 6) for field in ("slot_macro_f1", "numeric_recall", "cross_view_f1")}
        results[model]["sdcr_route_success_secondary"]["cases"] = len(routed)
    output = {"contract": "log2026.revised_answer_analysis.v1", "primary": "13-case ITT with routing/schema failures zero",
              "secondary": "four route-success cases; selection-conditioned and not confirmatory", "results": results}
    OUT.write_text(json.dumps(output, indent=2) + "\n"); print(OUT); return 0


if __name__ == "__main__": raise SystemExit(main())
