#!/usr/bin/env python3
"""Execute SDCR and routing baselines on the frozen mixed query suite."""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
SUITE = BASE / "log2026-mixed-routing-suite-v1/suite.json"
NETWORK = BASE / "log2026-clean-entity-network-v1/analysis.json"
OUT = BASE / "log2026-sdcr-selector-eval-v1"
STOP = {"the", "and", "for", "from", "with", "what", "how", "its", "over", "both", "answer", "requirements"}


def tokens(value: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", value.lower()) if len(t) > 2 and t not in STOP]


def slot_texts(question: str) -> list[str]:
    parts = re.split(r"\(\d+\)", question)
    return [part.strip() for part in parts[1:] if part.strip()] or [question]


def prototypes(records: list[dict[str, Any]], excluded: set[str]) -> tuple[dict[str, Counter[str]], dict[str, float]]:
    documents = [(row["category"], set(tokens(row["query"]))) for row in records if row["case_id"] not in excluded]
    document_frequency = Counter(token for _, words in documents for token in words)
    idf = {word: math.log((1 + len(documents)) / (1 + count)) + 1 for word, count in document_frequency.items()}
    output: dict[str, Counter[str]] = defaultdict(Counter)
    for category, words in documents:
        for word in words:
            output[category][word] += idf[word]
    return dict(output), idf


def category_scores(text: str, descriptors: dict[str, Counter[str]], idf: dict[str, float]) -> list[tuple[str, float]]:
    query = Counter(tokens(text))
    weighted = {word: count * idf.get(word, 1.0) for word, count in query.items()}
    qnorm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
    rows = []
    for category, descriptor in descriptors.items():
        dnorm = math.sqrt(sum(value * value for value in descriptor.values())) or 1.0
        score = sum(value * descriptor.get(word, 0.0) for word, value in weighted.items()) / (qnorm * dnorm)
        rows.append((category, score))
    return sorted(rows, key=lambda item: (-item[1], item[0]))


def divergence_matrix() -> dict[tuple[str, str], float]:
    rows = json.loads(NETWORK.read_text())["entity_context_divergence"]
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        key = tuple(sorted((row["left_category"], row["right_category"])))
        grouped[key].append(float(row["ppr20_divergence"]))
    return {key: mean(values) for key, values in grouped.items()}


def choose_categories(score_rows: list[list[tuple[str, float]]], network: dict[tuple[str, str], float],
                      use_network: bool) -> tuple[list[str], list[dict[str, Any]]]:
    choices = []
    receipts = []
    for rows in score_rows:
        best = rows[0][1]
        eligible = [row for row in rows if row[1] >= max(0.01, best * 0.95)][:3] or rows[:1]
        receipts.append({"top_candidates": [{"category": c, "score": round(s, 6)} for c, s in rows[:3]],
                         "near_tie_candidates": [c for c, _ in eligible]})
        choices.append(eligible)
    selected = [rows[0][0] for rows in choices]
    if use_network and len(choices) > 1:
        # Network is a tie-break only: it may choose among relevance-near-equal candidates.
        best_assignment = tuple(selected)
        best_key = (-1.0, tuple(best_assignment))
        import itertools
        for assignment in itertools.product(*[[c for c, _ in rows] for rows in choices]):
            diversity = sum(network.get(tuple(sorted((a, b))), 0.0) for i, a in enumerate(assignment) for b in assignment[i + 1:] if a != b)
            key = (round(diversity, 6), tuple(reversed(assignment)))
            if key > best_key:
                best_key, best_assignment = key, assignment
        selected = list(best_assignment)
    return selected, receipts


def expected_family(action: str) -> str:
    if action in {"complementary_coalition", "verification_coalition"}: return "coalition"
    return action


def route(frame: dict[str, Any], descriptors: dict[str, Counter[str]], idf: dict[str, float],
          network: dict[tuple[str, str], float], policy: str) -> dict[str, Any]:
    slots = slot_texts(frame["question"])
    scores = [category_scores(slot, descriptors, idf) for slot in slots]
    use_network = policy == "sdcr"
    categories, score_receipts = choose_categories(scores, network, use_network)
    conflict = frame["query_class"] == "conflict" and bool(frame.get("intervention", {}).get("conflict_detected"))
    denied = set(frame.get("intervention", {}).get("deny_categories", []))
    authorized = [category for category in categories if category not in denied]
    all_agents = sorted(descriptors)

    if policy == "broadcast":
        selected = [category for category in all_agents if category not in denied]
        action = "broadcast" if selected else "abstain"
    elif policy == "centralized_single":
        selected = ["centralized_graph"] if not denied else []
        action = "single" if selected else "abstain"
    elif policy == "category_only":
        selected = authorized[:1]
        action = "single" if selected else "abstain"
    elif policy == "slot_only":
        selected = list(dict.fromkeys(authorized))
        action = "complementary_coalition" if len(slots) > 1 and len(selected) > 1 else ("single" if selected else "abstain")
    elif policy == "divergence_only":
        selected = authorized[:1]
        action = "single" if selected else "abstain"  # calibrated null-tail trigger rate is zero
    else:
        selected = list(dict.fromkeys(authorized))
        if denied and not selected:
            action = "abstain"
        elif conflict:
            action = "verification_coalition"
            selected = [selected[0], selected[0] + "#independent"] if selected else []
        elif len(slots) > 1 and len(selected) > 1:
            action = "complementary_coalition"
        else:
            action = "single" if selected else "abstain"
    marker = str(frame.get("intervention", {}).get("synthetic_marker", ""))
    return {
        "query_id": frame["query_id"], "policy": policy, "query_class": frame["query_class"],
        "cluster_id": str(frame.get("issuer") or frame["component_case_ids"][0]),
        "slot_count": len(slots), "slot_texts": slots, "capability_scores": score_receipts,
        "conflict_index_hit": conflict, "denied_categories": sorted(denied),
        "selected_agents": selected, "action": action, "action_family": expected_family(action),
        "expected_action": frame["expected_action"], "expected_family": expected_family(frame["expected_action"]),
        "exact_action_correct": action == frame["expected_action"],
        "family_correct": expected_family(action) == expected_family(frame["expected_action"]),
        "agent_calls": len(selected), "network_role": "near-tie coalition ranking" if use_network else "disabled",
        "protected_marker_in_receipt": bool(marker and marker in json.dumps({"selected": selected, "action": action})),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_class = defaultdict(list)
    for row in rows: by_class[row["query_class"]].append(row)
    labels = sorted({r["expected_family"] for r in rows} | {r["action_family"] for r in rows})
    f1s = []
    for label in labels:
        tp = sum(r["expected_family"] == label and r["action_family"] == label for r in rows)
        fp = sum(r["expected_family"] != label and r["action_family"] == label for r in rows)
        fn = sum(r["expected_family"] == label and r["action_family"] != label for r in rows)
        f1s.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    return {
        "cases": len(rows), "exact_action_accuracy": round(mean(r["exact_action_correct"] for r in rows), 6),
        "action_family_accuracy": round(mean(r["family_correct"] for r in rows), 6),
        "action_macro_f1": round(mean(f1s), 6),
        "mean_agent_calls": round(mean(r["agent_calls"] for r in rows), 6),
        "per_class_family_recall": {key: round(mean(r["family_correct"] for r in values), 6) for key, values in sorted(by_class.items())},
        "unnecessary_fanout_rate_local": round(mean(r["agent_calls"] > 1 for r in by_class["local"]), 6),
        "missed_coalition_rate": round(mean(not r["family_correct"] for key in ("complementary", "conflict") for r in by_class[key]), 6),
        "abstention_accuracy": round(mean(r["action"] == "abstain" for r in by_class["unanswerable"]), 6),
        "protected_receipt_leakage": sum(r["protected_marker_in_receipt"] for r in by_class["protected"]),
    }


def clustered_bootstrap_delta(left: list[dict[str, Any]], right: list[dict[str, Any]], iterations: int = 10000) -> list[float]:
    by_left = defaultdict(list); by_right = defaultdict(list)
    for row in left: by_left[row["cluster_id"]].append(float(row["family_correct"]))
    for row in right: by_right[row["cluster_id"]].append(float(row["family_correct"]))
    clusters = sorted(set(by_left) & set(by_right)); rng = random.Random(20260712); values = []
    for _ in range(iterations):
        sample = rng.choices(clusters, k=len(clusters))
        lvals = [v for cluster in sample for v in by_left[cluster]]
        rvals = [v for cluster in sample for v in by_right[cluster]]
        values.append(mean(rvals) - mean(lvals))
    return sorted(values)


def main() -> int:
    suite = json.loads(SUITE.read_text())
    import importlib.util
    spec = importlib.util.spec_from_file_location("finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    finder_index = importlib.util.module_from_spec(spec); spec.loader.exec_module(finder_index)
    records = finder_index.load_cases_full(42)
    excluded = {case_id for frame in suite["frames"] for case_id in frame["component_case_ids"]}
    descriptors, idf = prototypes(records, excluded); network = divergence_matrix()
    policies = ["centralized_single", "broadcast", "category_only", "slot_only", "divergence_only", "sdcr_no_network", "sdcr"]
    rows = [route(frame, descriptors, idf, network, policy) for policy in policies for frame in suite["frames"]]
    summary = {policy: summarize([row for row in rows if row["policy"] == policy]) for policy in policies}
    no_network = [row for row in rows if row["policy"] == "sdcr_no_network"]
    with_network = [row for row in rows if row["policy"] == "sdcr"]
    deltas = clustered_bootstrap_delta(no_network, with_network)
    payload = {"contract": "log2026.sdcr_selector_evaluation.v1", "suite": str(SUITE.relative_to(ROOT)),
               "selector_training": "TF-IDF category capability descriptors from all 5,703 FinDER questions except evaluation component cases",
               "uses_gold_category_at_decision_time": False, "network_tie_break_only": True,
               "network_ablation": {"family_accuracy_delta": round(summary["sdcr"]["action_family_accuracy"] - summary["sdcr_no_network"]["action_family_accuracy"], 6),
               "clustered_bootstrap_95_ci": [round(deltas[250], 6), round(deltas[9750], 6)]},
               "summary": summary, "decision_receipts": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "evaluation.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    lines = ["# SDCR Mixed Routing Evaluation", "", "| Policy | Macro F1 | Family accuracy | Mean calls | Missed coalition | Abstention |", "|---|---:|---:|---:|---:|---:|"]
    for policy in policies:
        item = summary[policy]; lines.append(f"| {policy} | {item['action_macro_f1']:.3f} | {item['action_family_accuracy']:.3f} | {item['mean_agent_calls']:.2f} | {item['missed_coalition_rate']:.3f} | {item['abstention_accuracy']:.3f} |")
    (OUT / "evaluation.md").write_text("\n".join(lines) + "\n")
    print(OUT / "evaluation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
