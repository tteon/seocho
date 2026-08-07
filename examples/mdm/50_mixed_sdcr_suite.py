#!/usr/bin/env python3
"""Freeze a mixed routing suite from existing FinDER and intervention artifacts."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
OUT = BASE / "log2026-mixed-routing-suite-v1"


def load_local_cases() -> list[dict[str, Any]]:
    source = json.loads((BASE / "fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())
    return sorted(source["records"], key=lambda row: row["case_id"])[:28]


def build() -> dict[str, Any]:
    local_cases = load_local_cases()
    candidates = json.loads((BASE / "log2026-full-finder-cross-view-v1/candidates.json").read_text())
    by_candidate = {row["candidate_id"]: row for row in candidates["candidates"]}
    adjudication = json.loads((BASE / "log2026-full-finder-cross-view-v1/heldout_author_adjudication.json").read_text())
    accepted = [by_candidate[row["candidate_id"]] for row in adjudication["rows"] if row["decision"] == "accept"]
    verification = json.loads((BASE / "log2026-sdcr-verification-v1/verification.json").read_text())["cases"]
    local_by_id = {row["case_id"]: row for row in local_cases}
    all_source = {
        row["case_id"]: row for row in
        json.loads((BASE / "fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())["records"]
    }
    frames: list[dict[str, Any]] = []
    for row in local_cases:
        frames.append({
            "query_id": f"local-{row['case_id']}", "query_class": "local", "question": row["query"],
            "component_case_ids": [row["case_id"]], "required_categories": [row["category"]],
            "required_slots": ["original_finder_answer"], "gold_answers": [row["answer"]],
            "expected_action": "single", "label_source": "FinDER source category", "natural": True,
        })
    for row in accepted:
        frames.append({
            "query_id": f"cross-{row['candidate_id']}", "query_class": "complementary", "question":
            f"For {row['issuer']}, answer both requirements: (1) {row['component_questions'][0]} (2) {row['component_questions'][1]}",
            "component_case_ids": row["component_case_ids"], "required_categories": row["required_categories"],
            "required_slots": ["component_answer_1", "component_answer_2"], "gold_answers": row["required_gold_slots"],
            "expected_action": "complementary_coalition", "label_source": "provenance gate plus author adjudication",
            "natural": False, "issuer": row["issuer"],
        })
    for row in verification:
        source = all_source[row["case_id"]]
        frames.append({
            "query_id": row["query_id"], "query_class": "conflict", "question": source["query"],
            "component_case_ids": [row["case_id"]], "required_categories": [row["category"]],
            "required_slots": ["verified_numeric_fact"], "gold_answers": [source["answer"]],
            "expected_action": "verification_coalition", "label_source": "pre-registered comparable-fact intervention",
            "natural": False, "intervention": row,
        })
        frames.append({
            "query_id": f"protected-{row['case_id']}", "query_class": "protected", "question": source["query"],
            "component_case_ids": [row["case_id"]], "required_categories": [row["category"]],
            "required_slots": ["original_finder_answer"], "gold_answers": [source["answer"]],
            "expected_action": "single", "label_source": "pre-registered protected-field intervention",
            "natural": False, "intervention": {"target_provider": row["target_provider"],
            "synthetic_marker": "PROTECTED_" + row["case_id"], "policy": "deny_cross_agent_disclosure"},
        })
    for row in local_cases[:8]:
        frames.append({
            "query_id": f"denied-{row['case_id']}", "query_class": "unanswerable", "question": row["query"],
            "component_case_ids": [row["case_id"]], "required_categories": [row["category"]],
            "required_slots": ["original_finder_answer"], "gold_answers": [row["answer"]],
            "expected_action": "abstain", "label_source": "pre-registered authorization intervention",
            "natural": False, "intervention": {"deny_categories": [row["category"]], "policy": "no_authorized_slot_cover"},
        })
    counts = Counter(row["query_class"] for row in frames)
    return {
        "contract": "log2026.mixed_routing_suite.v1", "frozen": True, "selection_uses_model_outputs": False,
        "seed": 42, "counts": dict(sorted(counts.items())), "frames": frames,
        "evaluation": {"primary": "macro-F1 over expected_action", "secondary": [
            "per-class recall", "unnecessary fan-out rate", "missed coalition rate", "abstention accuracy",
            "protected-marker disclosure", "decision cost"]},
        "review_status": "labels provenance-frozen; independent reviewer-blind validation pending",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = build()
    (OUT / "suite.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Mixed SDCR Routing Suite", "", f"Total: {len(payload['frames'])}", ""]
    lines.extend(f"- {key}: {value}" for key, value in payload["counts"].items())
    lines += ["", "Selection and labels use only source categories, frozen provenance/adjudication, and pre-registered interventions. Model outputs are not used.", ""]
    (OUT / "README.md").write_text("\n".join(lines))
    print(OUT / "suite.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
