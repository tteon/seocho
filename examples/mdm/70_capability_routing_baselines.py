#!/usr/bin/env python3
"""Compare data-derived capability routing with matched random selection.

All policies use the frozen 13 revised cases and exact 2,048-token evidence
budget. Random policies sample from all eight category agents; categories with
no required-view evidence correctly contribute no evidence. This is an
evaluation baseline, not a learned model.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
from pathlib import Path
from statistics import mean

import tiktoken


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "capability_routing_baselines.json"
os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp/tiktoken")
CATEGORIES = (
    "Accounting", "Company overview", "Financials", "Footnotes",
    "Governance", "Legal", "Risk", "Shareholder return",
)


def load_exact():
    spec = importlib.util.spec_from_file_location("exact", ROOT / "examples/mdm/53_exact_token_retrieval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_selected(item: dict, selected: tuple[str, ...], exact, encoder, *, require_complete: bool = False) -> dict:
    left_category, right_category = item["required_categories"]
    left = item["arms"]["left_single"]["evidence"]
    right = item["arms"]["right_single"]["evidence"]
    required = {left_category, right_category}
    by_category = {left_category: left, right_category: right}
    if require_complete and not required.issubset(selected):
        order = []
        evidence, tokens = exact.cap(order, 2048, encoder)
        coverage = [exact.coverage(evidence, gold)[0] for gold in item["golds"]]
        return {
            "required_view_coverage": len(required & set(selected)) / len(required),
            "slot_token_recall": mean(coverage[:2]),
            "cross_view_token_recall": coverage[2],
            "tokens_used": tokens,
        }
    # Preserve the policy's output order.  The first serialized item can
    # determine what survives the fixed evidence cap.
    chosen = [by_category[category] for category in selected if category in by_category]
    if len(chosen) == 2:
        order = exact.alternate(chosen[0], chosen[1])
    elif chosen:
        order = chosen[0]
    else:
        order = []
    evidence, tokens = exact.cap(order, 2048, encoder)
    coverage = [exact.coverage(evidence, gold)[0] for gold in item["golds"]]
    return {
        "required_view_coverage": len(required & set(selected)) / len(required),
        "slot_token_recall": mean(coverage[:2]),
        "cross_view_token_recall": coverage[2],
        "tokens_used": tokens,
    }


def average(rows: list[dict]) -> dict:
    return {key: round(mean(row[key] for row in rows), 6) for key in rows[0]}


def main() -> int:
    exact = load_exact()
    encoder = tiktoken.get_encoding("cl100k_base")
    payload = json.loads((BASE / "revised_exact_retrieval.json").read_text())
    rows = payload["rows"]
    policies: dict[str, list[dict]] = {
        "tfidf_top1": [],
        "tfidf_top2": [],
        "actual_sdcr": [],
        "oracle_minimal_team": [],
        "random_authorized_1": [],
        "random_authorized_2": [],
        "random_authorized_3": [],
    }
    for item in rows:
        policies["tfidf_top1"].append(score_selected(item, tuple(item["category_choice"]), exact, encoder))
        policies["tfidf_top2"].append(score_selected(item, tuple(item["slot_choices"]), exact, encoder))
        policies["actual_sdcr"].append(score_selected(item, tuple(item["sdcr_choices"]), exact, encoder, require_complete=True))
        policies["oracle_minimal_team"].append(score_selected(item, tuple(item["required_categories"]), exact, encoder))
        for k in (1, 2, 3):
            scores = [score_selected(item, selected, exact, encoder) for selected in itertools.combinations(CATEGORIES, k)]
            policies[f"random_authorized_{k}"].append(average(scores))
    summary = {policy: average(values) for policy, values in policies.items()}
    result = {
        "contract": "log2026.capability_routing_baselines.v1",
        "cases": len(rows),
        "candidate_pool": list(CATEGORIES),
        "budget": {"tokens": 2048, "tokenizer": "cl100k_base"},
        "policy_definitions": {
            "tfidf_top1": "frozen one-category TF-IDF capability selector",
            "tfidf_top2": "frozen two-category TF-IDF capability selector",
            "actual_sdcr": "frozen LiteLLM router selection; uncovered cases retain ITT failure through its evidence arm elsewhere",
            "oracle_minimal_team": "required validated categories",
            "random_authorized_k": "uniform average over every k-category subset of the eight-agent pool",
        },
        "summary": summary,
        "claim_boundary": "The 13 cases are persona-screened rather than independently human-labeled. These results characterize routing coverage and retrieval only.",
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
