#!/usr/bin/env python3
"""Separate category, prompt, ontology, and model effects in the 1,280-cell matrix."""
from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/evaluation/mdm_fedcat/fedcat-full-matrix-v1/index_partial"
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-factorial-effects-v1"
GROUPS = ("category", "prompt", "ontology", "model", "prompt_x_ontology")


def load() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(SOURCE.glob("*.json")):
        row = json.loads(path.read_text()); prompt, ontology = str(row["scenario_id"]).split("__", 1)
        rows.append({**row, "prompt": prompt, "ontology": ontology})
    return rows


def design(rows: list[dict[str, Any]], included: set[str]) -> np.ndarray:
    columns = [np.ones(len(rows))]
    levels = {group: sorted({str(row[group]) for row in rows}) for group in ("category", "prompt", "ontology", "model")}
    for group in ("category", "prompt", "ontology", "model"):
        if group in included:
            for level in levels[group][1:]: columns.append(np.array([float(str(row[group]) == level) for row in rows]))
    if "prompt_x_ontology" in included:
        for prompt in levels["prompt"][1:]:
            for ontology in levels["ontology"][1:]:
                columns.append(np.array([float(str(row["prompt"]) == prompt and str(row["ontology"]) == ontology) for row in rows]))
    return np.column_stack(columns)


def sse(rows: list[dict[str, Any]], field: str, groups: set[str]) -> float:
    x = design(rows, groups); y = np.array([float(row[field]) for row in rows]); beta = np.linalg.lstsq(x, y, rcond=None)[0]
    return float(np.sum((y - x @ beta) ** 2))


def partial_r2(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    full_groups = set(GROUPS); full = sse(rows, field, full_groups); output = {}
    for group in GROUPS:
        reduced = sse(rows, field, full_groups - {group})
        output[group] = max(0.0, (reduced - full) / reduced) if reduced else 0.0
    return output


def bootstrap(rows: list[dict[str, Any]], field: str, draws: int = 1000) -> dict[str, list[float]]:
    by_case = {case: [row for row in rows if row["case_id"] == case] for case in sorted({row["case_id"] for row in rows})}
    cases = list(by_case); rng = random.Random(20260712); values = {group: [] for group in GROUPS}
    for _ in range(draws):
        sampled = []
        for index, case in enumerate(rng.choices(cases, k=len(cases))):
            sampled.extend({**row, "case_id": f"{case}#{index}"} for row in by_case[case])
        result = partial_r2(sampled, field)
        for group in GROUPS: values[group].append(result[group])
    return {group: [round(sorted(items)[25], 6), round(sorted(items)[975], 6)] for group, items in values.items()}


def main() -> int:
    rows = load()
    if len(rows) != 1280 or any(row.get("error") for row in rows): raise SystemExit("factorial matrix incomplete")
    results = {}
    for field in ("nodes_created", "rels_created", "latency_s"):
        point = partial_r2(rows, field); intervals = bootstrap(rows, field)
        results[field] = {group: {"partial_r2": round(point[group], 6), "case_clustered_bootstrap_95_ci": intervals[group]} for group in GROUPS}
    payload = {"contract": "log2026.factorial_effect_separation.v1", "records": len(rows), "cases": 16,
               "balanced_design": "8 categories x 2 cases x 4 prompts x 5 ontologies x 4 models",
               "model": "categorical OLS with category, prompt, ontology, model, and prompt-by-ontology interaction",
               "estimand": "partial R2 (incremental in-sample variance explained); descriptive, not a causal effect or answer improvement",
               "results": results}
    OUT.mkdir(parents=True, exist_ok=True); (OUT / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n")
    lines = ["# Factorial Effect Separation", "", "Partial R2 is descriptive graph-construction variance, not answer improvement.", ""]
    for field, groups in results.items():
        lines += [f"## {field}", "", "| Factor | Partial R2 | Case-clustered 95% CI |", "|---|---:|---:|"]
        for group, value in groups.items(): lines.append(f"| {group} | {value['partial_r2']:.3f} | {value['case_clustered_bootstrap_95_ci']} |")
        lines.append("")
    (OUT / "analysis.md").write_text("\n".join(lines)); print(OUT / "analysis.json"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
