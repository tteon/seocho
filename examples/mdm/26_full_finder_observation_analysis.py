#!/usr/bin/env python3
"""Analyze all frozen FinDER observation-policy extraction artifacts.

This is a zero-cost, file-only analysis. It combines:

* 5,703 FinDER cases x 4 models under baseline and survivorship profiles; and
* 16 cases x 4 prompts x 5 ontologies x 4 models in the full factorial matrix.

The FinDER case is the bootstrap unit. Model repetitions for the same case are
averaged before confidence intervals are computed, avoiding pseudo-replication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from statistics import mean, median
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
FEDCAT = ROOT / "outputs/evaluation/mdm_fedcat"
BASELINE = FEDCAT / "fedcat-full-all-baseline-v1/index_partial"
SURVIVORSHIP = FEDCAT / "fedcat-full-all-survivorship-v1/index_partial"
FACTORIAL = FEDCAT / "fedcat-full-matrix-v1/index_partial"
OUTPUT = FEDCAT / "log2026-full-finder-observation-v1"


def load_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record["case_id"]), str(record["model"])


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def case_bootstrap_ci(values: dict[str, float], seed: int = 42, draws: int = 2_000) -> list[float]:
    case_ids = sorted(values)
    rng = Random(seed)
    samples = [
        mean(values[case_ids[rng.randrange(len(case_ids))]] for _ in case_ids)
        for _ in range(draws)
    ]
    return [round(percentile(samples, 0.025), 4), round(percentile(samples, 0.975), 4)]


def profile_pair_analysis(
    baseline: list[dict[str, Any]], survivorship: list[dict[str, Any]]
) -> dict[str, Any]:
    left, right = {key(row): row for row in baseline}, {key(row): row for row in survivorship}
    paired = sorted(set(left) & set(right))
    if len(paired) != len(left) or len(paired) != len(right):
        raise ValueError("baseline and survivorship records are not fully paired")
    metrics: dict[str, Any] = {}
    for field in ("nodes_created", "rels_created", "latency_s"):
        by_case: dict[str, list[float]] = defaultdict(list)
        raw_deltas: list[float] = []
        for item in paired:
            delta = float(right[item][field]) - float(left[item][field])
            raw_deltas.append(delta)
            by_case[item[0]].append(delta)
        case_deltas = {case_id: mean(values) for case_id, values in by_case.items()}
        metrics[field] = {
            "baseline_mean": round(mean(float(left[item][field]) for item in paired), 4),
            "survivorship_mean": round(mean(float(right[item][field]) for item in paired), 4),
            "paired_case_mean_delta": round(mean(case_deltas.values()), 4),
            "case_bootstrap_95_ci": case_bootstrap_ci(case_deltas),
            "record_win_tie_loss": [
                sum(value > 0 for value in raw_deltas),
                sum(value == 0 for value in raw_deltas),
                sum(value < 0 for value in raw_deltas),
            ],
        }
    by_category: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in paired:
        category = str(left[item]["category"])
        for field in ("nodes_created", "rels_created"):
            by_category[category][field].append(float(right[item][field]) - float(left[item][field]))
    return {
        "cases": len({item[0] for item in paired}),
        "models": len({item[1] for item in paired}),
        "paired_records": len(paired),
        "errors": {
            "baseline": sum(bool(row.get("error")) for row in baseline),
            "survivorship": sum(bool(row.get("error")) for row in survivorship),
        },
        "metrics": metrics,
        "category_mean_deltas": {
            category: {field: round(mean(values), 4) for field, values in fields.items()}
            for category, fields in sorted(by_category.items())
        },
    }


def split_scenario(scenario_id: str) -> tuple[str, str]:
    prompt, ontology = scenario_id.split("__", 1)
    return prompt, ontology


def factorial_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in records:
        prompt, ontology = split_scenario(str(row["scenario_id"]))
        cells[(str(row["case_id"]), str(row["model"]), prompt, ontology)] = row
    prompts = sorted({item[2] for item in cells})
    ontologies = sorted({item[3] for item in cells})
    models = sorted({item[1] for item in cells})
    cases = sorted({item[0] for item in cells})
    expected = len(prompts) * len(ontologies) * len(models) * len(cases)
    if len(cells) != expected:
        raise ValueError(f"factorial matrix incomplete: {len(cells)}/{expected}")

    def marginal(group: str, level: str, field: str) -> float:
        index = 2 if group == "prompt" else 3
        return mean(float(row[field]) for item, row in cells.items() if item[index] == level)

    marginal_means: dict[str, Any] = {}
    for field in ("nodes_created", "rels_created", "latency_s"):
        marginal_means[field] = {
            "prompt": {level: round(marginal("prompt", level, field), 4) for level in prompts},
            "ontology": {level: round(marginal("ontology", level, field), 4) for level in ontologies},
            "model": {
                level: round(mean(float(row[field]) for item, row in cells.items() if item[1] == level), 4)
                for level in models
            },
        }

    # Difference-in-differences relative to neutral prompt and generic ontology.
    interactions: dict[str, dict[str, float]] = {}
    neutral, generic = "neutral_kg@v1", "generic_baseline"
    for field in ("nodes_created", "rels_created"):
        result: dict[str, float] = {}
        for prompt in prompts:
            if prompt == neutral:
                continue
            for ontology in ontologies:
                if ontology == generic:
                    continue
                values = []
                for case_id in cases:
                    for model in models:
                        value = (
                            float(cells[(case_id, model, prompt, ontology)][field])
                            - float(cells[(case_id, model, neutral, ontology)][field])
                            - float(cells[(case_id, model, prompt, generic)][field])
                            + float(cells[(case_id, model, neutral, generic)][field])
                        )
                        values.append(value)
                result[f"{prompt} x {ontology}"] = round(mean(values), 4)
        interactions[field] = dict(sorted(result.items()))
    return {
        "records": len(records),
        "cases": len(cases),
        "prompts": prompts,
        "ontologies": ontologies,
        "models": models,
        "expected_records": expected,
        "errors": sum(bool(row.get("error")) for row in records),
        "marginal_means": marginal_means,
        "prompt_ontology_difference_in_differences": interactions,
        "scope": "graph-construction outcomes only; retrieval and answer effects require downstream joins",
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    pair = payload["full_finder_profile_pair"]
    fact = payload["full_factorial"]
    lines = [
        "# Full FinDER Observation-Policy Analysis",
        "",
        "## Data audit",
        "",
        f"- Full FinDER cases: {pair['cases']:,}",
        f"- Models per profile: {pair['models']}",
        f"- Paired records per two-profile comparison: {pair['paired_records']:,}",
        f"- Full factorial records: {fact['records']:,}/{fact['expected_records']:,}",
        f"- Errors: baseline {pair['errors']['baseline']}, survivorship {pair['errors']['survivorship']}, factorial {fact['errors']}",
        "",
        "## Full FinDER paired profile comparison",
        "",
        "| Outcome | Baseline mean | Survivorship mean | Case-paired delta | 95% bootstrap CI | Record W/T/L |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for field, row in pair["metrics"].items():
        lines.append(
            f"| {field} | {row['baseline_mean']:.4f} | {row['survivorship_mean']:.4f} | "
            f"{row['paired_case_mean_delta']:+.4f} | {row['case_bootstrap_95_ci']} | "
            f"{'/'.join(map(str, row['record_win_tie_loss']))} |"
        )
    lines.extend(["", "## Full factorial marginal means", ""])
    for field, groups in fact["marginal_means"].items():
        lines.extend([f"### {field}", ""])
        for group, values in groups.items():
            rendered = ", ".join(f"`{name}`={value:.4f}" for name, value in values.items())
            lines.append(f"- {group}: {rendered}")
        lines.append("")
    lines.extend(
        [
            "## Claim boundary",
            "",
            "These file-level results establish graph-construction effects. Node/edge expansion is not answer improvement. PPR, typed-path, slot, and answer analyses must be joined before making the downstream multi-agent claim.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--survivorship", type=Path, default=SURVIVORSHIP)
    parser.add_argument("--factorial", type=Path, default=FACTORIAL)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    baseline, survivorship, factorial = (
        load_records(args.baseline), load_records(args.survivorship), load_records(args.factorial)
    )
    payload = {
        "schema_version": "log2026.full_finder_observation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_hash": hashlib.sha256(
            f"{len(baseline)}:{len(survivorship)}:{len(factorial)}".encode()
        ).hexdigest(),
        "full_finder_profile_pair": profile_pair_analysis(baseline, survivorship),
        "full_factorial": factorial_analysis(factorial),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_report(args.output / "analysis.md", payload)
    print(args.output / "analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
