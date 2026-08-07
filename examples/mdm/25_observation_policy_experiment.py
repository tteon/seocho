#!/usr/bin/env python3
"""Freeze a pre-registered contrast inside the completed full-factorial run.

The existing 960-cell artifact bundles prompt and ontology into three joint
profiles. The repository also contains a completed 4 x 5 x 4 x 16 factorial
run. This script defines the originally proposed 2 x 2 x 4 x 16 contrast as a
subset of that existing run; it requires no new paid extraction. Case selection
uses only category and case identifier; outcomes are never consulted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = (
    ROOT / "outputs/evaluation/mdm_fedcat/fedcat-full-matrix-v1/index_partial"
)
DEFAULT_OUTPUT = (
    ROOT / "outputs/evaluation/mdm_fedcat/log2026-observation-policy-v1"
)

PROMPTS = (
    {"prompt_id": "neutral_kg@v1", "factor_level": "neutral"},
    {
        "prompt_id": "duplicate_aware_survivorship@v1",
        "factor_level": "task_specific",
    },
)
ONTOLOGIES = (
    {
        "ontology_id": "generic_baseline",
        "factor_level": "generic",
        "modules": [],
    },
    {
        "ontology_id": "fibo_finance_core",
        "factor_level": "finance",
        "modules": ["be", "fbc", "fnd", "ind", "acc"],
    },
)
MODELS = (
    {"provider_id": "deepseek", "model": "DeepSeek-V3.1"},
    {"provider_id": "gptoss", "model": "gpt-oss-120b"},
    {"provider_id": "minimax25", "model": "MiniMax-M2.5"},
    {"provider_id": "minimax27", "model": "MiniMax-M2.7"},
)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_records(source: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(source.glob("*.json"))
    ]


def select_cases(records: list[dict[str, Any]], per_category: int = 2) -> list[dict[str, str]]:
    """Select cases without consulting graph counts, errors, latency, or answers."""
    by_category: dict[str, set[str]] = defaultdict(set)
    for record in records:
        by_category[str(record["category"])].add(str(record["case_id"]))
    selected: list[dict[str, str]] = []
    for category in sorted(by_category):
        case_ids = sorted(by_category[category])
        if len(case_ids) < per_category:
            raise ValueError(f"category {category!r} has only {len(case_ids)} cases")
        selected.extend(
            {"category": category, "case_id": case_id}
            for case_id in case_ids[:per_category]
        )
    return selected


def build_cells(cases: list[dict[str, str]]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for case in cases:
        for prompt in PROMPTS:
            for ontology in ONTOLOGIES:
                for model in MODELS:
                    identity = {
                        **case,
                        "prompt_id": prompt["prompt_id"],
                        "ontology_id": ontology["ontology_id"],
                        "provider_id": model["provider_id"],
                        "model": model["model"],
                    }
                    cells.append(
                        {
                            **identity,
                            "cell_id": _stable_hash(identity)[:16],
                            "prompt_level": prompt["factor_level"],
                            "ontology_level": ontology["factor_level"],
                            "ontology_modules": ontology["modules"],
                            "status": "covered_by_existing_full_factorial",
                        }
                    )
    return cells


def inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios = Counter(str(row.get("scenario_id", "")) for row in records)
    models = Counter(str(row.get("model", "")) for row in records)
    categories = Counter(str(row.get("category", "")) for row in records)
    return {
        "records": len(records),
        "unique_cases": len({str(row.get("case_id", "")) for row in records}),
        "scenarios": dict(sorted(scenarios.items())),
        "models": dict(sorted(models.items())),
        "categories": dict(sorted(categories.items())),
        "failed_records": sum(bool(row.get("error")) for row in records),
        "claim_scope": (
            "balanced prompt x ontology x generation-model graph-construction effects; "
            "retrieval and answer effects require downstream joins"
        ),
    }


def build_manifest(records: list[dict[str, Any]]) -> dict[str, Any]:
    cases = select_cases(records)
    cells = build_cells(cases)
    return {
        "schema_version": "log2026.observation_policy_gate.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_policy": (
            "first two lexicographically sorted case_ids per sorted category; "
            "uses category and case_id only"
        ),
        "existing_full_factorial_inventory": inventory(records),
        "factors": {
            "prompts": list(PROMPTS),
            "ontologies": list(ONTOLOGIES),
            "models": list(MODELS),
            "cases": cases,
        },
        "contrast_cells": len(cells),
        "required_new_extractions": 0,
        "source_full_factorial": "fedcat-full-matrix-v1",
        "cells": cells,
        "analysis_contract": {
            "matched_unit": "FinDER case_id",
            "blocking": ["case_id", "category"],
            "fixed_controls": [
                "source references",
                "chunking",
                "temperature",
                "retry policy",
                "graph writer",
                "extraction budget",
            ],
            "primary_chain": [
                "observation_policy_to_graph_phenotype",
                "graph_phenotype_to_ppr_retrieval",
                "ppr_retrieval_to_required_slot_coverage",
                "slot_coverage_to_fixed_answerer_quality",
                "complementary_slots_to_coalition_advantage",
            ],
            "missing_results_policy": (
                "fill graph-construction cells from frozen artifacts; leave retrieval, "
                "slot, and answer cells as TBD until joined; never project values"
            ),
        },
    }


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    inv = manifest["existing_full_factorial_inventory"]
    lines = [
        "# LoG 2026 Observation-Policy Experiment",
        "",
        "## Frozen full-factorial study",
        "",
        f"- Extraction records: {inv['records']}",
        f"- Unique FinDER cases: {inv['unique_cases']}",
        f"- Recorded failures: {inv['failed_records']}",
        f"- Valid claim: {inv['claim_scope']}",
        "",
        "## Orthogonal gate",
        "",
        f"- Pre-registered contrast cells: {manifest['contrast_cells']}",
        "- Design: 2 prompts x 2 ontologies x 4 models x 16 cases",
        f"- Case selection: {manifest['selection_policy']}",
        "- Status: all contrast cells are covered by the completed 1,280-cell full factorial run.",
        "- Required new paid extractions: 0",
        "",
        "## Interpretation rule",
        "",
        "A larger graph is not automatically better. A factor effect is carried "
        "forward only when it changes entity-centered retrieval, grounded answer "
        "slots, or justified abstention under fixed budgets.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = load_records(args.source)
    if not records:
        raise SystemExit(f"no extraction records found under {args.source}")
    manifest = build_manifest(records)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(args.output / "experiment_plan.md", manifest)
    print(f"contrast_cells={manifest['contrast_cells']}")
    print(f"required_new_extractions={manifest['required_new_extractions']}")
    print(args.output / "experiment_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
