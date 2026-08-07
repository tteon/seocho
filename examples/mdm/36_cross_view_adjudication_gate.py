#!/usr/bin/env python3
"""Freeze only independently reviewed cross-view candidates for evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-finder-cross-view-v1"
REQUIRED = {"both_views_required", "single_view_sufficient", "financially_natural", "gold_slots_valid", "decision", "rationale"}


def valid_review(review: Any) -> bool:
    if not isinstance(review, dict) or not REQUIRED <= review.keys():
        return False
    return (
        review["both_views_required"] in {"yes", "no", "uncertain"}
        and review["single_view_sufficient"] in {"yes", "no", "uncertain"}
        and review["financially_natural"] in {"yes", "no", "revise"}
        and review["gold_slots_valid"] in {"yes", "no", "uncertain"}
        and review["decision"] in {"accept", "revise", "reject"}
        and bool(str(review["rationale"]).strip())
    )


def accepted(adjudicated: Any) -> bool:
    return bool(
        valid_review(adjudicated)
        and adjudicated["decision"] == "accept"
        and adjudicated["both_views_required"] == "yes"
        and adjudicated["single_view_sufficient"] == "no"
        and adjudicated["financially_natural"] == "yes"
        and adjudicated["gold_slots_valid"] == "yes"
    )


def freeze(candidates: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    by_id = {row["candidate_id"]: row for row in candidates["candidates"]}
    rows = []
    for annotation in annotations["annotations"]:
        r1, r2, final = annotation.get("reviewer_1"), annotation.get("reviewer_2"), annotation.get("adjudicated")
        complete = valid_review(r1) and valid_review(r2) and valid_review(final)
        rows.append({
            "candidate_id": annotation["candidate_id"],
            "independent_reviews_complete": complete,
            "accepted": complete and accepted(final),
            "candidate": by_id[annotation["candidate_id"]] if complete and accepted(final) else None,
        })
    return {
        "contract": "log2026.cross_view_frozen.v1",
        "candidate_count": len(rows),
        "complete_count": sum(row["independent_reviews_complete"] for row in rows),
        "accepted_count": sum(row["accepted"] for row in rows),
        "evaluation_unlocked": any(row["accepted"] for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=BASE / "candidates.json")
    parser.add_argument("--annotations", type=Path, default=BASE / "annotations.json")
    parser.add_argument("--output", type=Path, default=BASE / "frozen_candidates.json")
    args = parser.parse_args()
    payload = freeze(json.loads(args.candidates.read_text()), json.loads(args.annotations.read_text()))
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"complete={payload['complete_count']}/{payload['candidate_count']} accepted={payload['accepted_count']} unlocked={payload['evaluation_unlocked']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
