#!/usr/bin/env python3
"""Quality gate for diverse customer-query and boundary-case corpora."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from seocho.eval.customer_query_dataset import classify_customer_query


def _normalized(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.lower()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--challenges", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line]
    challenges = [json.loads(line) for line in args.challenges.read_text().splitlines() if line]
    correct = Counter()
    totals = Counter()
    confusion = Counter()
    for row in rows:
        split = row["split"]
        routed = classify_customer_query(row["question"])
        expected = row["gold"]["intent"]
        observed = routed.intent if routed else "abstain"
        totals[split] += 1
        correct[split] += observed == expected
        if observed != expected:
            confusion[f"{expected}->{observed}"] += 1
    challenge_actions = Counter(row["gold"]["expected_action"] for row in challenges)
    challenge_kinds = Counter(row["gold"]["kind"] for row in challenges)
    unique = len({row["question"] for row in rows})
    normalized_unique = len({_normalized(row["question"]) for row in rows})
    challenge_unique = len({row["question"] for row in challenges})
    family_counts = Counter(row["template_family"] for row in rows)
    evaluation_accuracy = correct["evaluation"] / totals["evaluation"]
    held_out_accuracy = correct["held_out"] / totals["held_out"]
    passed = (
        unique == len(rows)
        and normalized_unique == len(rows)
        and challenge_unique == len(challenges)
        and len(family_counts) >= 50
        and min(family_counts.values()) >= 100
        and evaluation_accuracy >= 0.90
        and held_out_accuracy >= 0.90
        and set(challenge_actions) == {"clarify", "decompose", "reject"}
    )
    report = {
        "schema_version": "seocho.customer-query-diversity.v1",
        "queries": len(rows),
        "unique_questions": unique,
        "normalized_unique_questions": normalized_unique,
        "exact_duplicate_rate": 1 - unique / len(rows),
        "template_families": len(family_counts),
        "family_size": {"min": min(family_counts.values()), "max": max(family_counts.values())},
        "routing_accuracy": {
            "evaluation": evaluation_accuracy,
            "held_out_family": held_out_accuracy,
        },
        "routing_confusion": dict(confusion.most_common()),
        "challenges": {
            "queries": len(challenges),
            "unique_questions": challenge_unique,
            "kinds": dict(challenge_kinds),
            "expected_actions": dict(challenge_actions),
        },
        "passed": passed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
