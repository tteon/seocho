#!/usr/bin/env python3
"""Cost sensitivity and specialist-dropout replay over frozen selector receipts."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
SOURCE = BASE / "log2026-sdcr-selector-eval-v1/evaluation.json"
OUT = BASE / "log2026-selector-robustness-v1"


def main() -> int:
    payload = json.loads(SOURCE.read_text()); rows = payload["decision_receipts"]
    by_policy = {policy: [row for row in rows if row["policy"] == policy] for policy in payload["summary"]}
    sensitivity = []
    for weight in (0, .01, .025, .05, .1, .2):
        utilities = {policy: mean(float(row["family_correct"]) - weight * row["agent_calls"] for row in values)
                     for policy, values in by_policy.items()}
        best = max(utilities, key=lambda policy: (utilities[policy], policy))
        sensitivity.append({"cost_weight": weight, "best_policy": best,
                            "utilities": {key: round(value, 6) for key, value in utilities.items()}})
    coalition = [row for row in by_policy["sdcr"] if row["action_family"] == "coalition"]
    dropout_rows = []
    for row in coalition:
        original = row["selected_agents"]; dropped = original[0] if original else None
        alternatives = []
        for slot in row["capability_scores"]:
            alternatives.append(next((candidate["category"] for candidate in slot["top_candidates"] if candidate["category"] != dropped), None))
        reformed = list(dict.fromkeys(category for category in alternatives if category))
        required_multiplicity = 2 if row["expected_family"] == "coalition" else 1
        dropout_rows.append({"query_id": row["query_id"], "dropped_agent": dropped, "reformed_agents": reformed,
                             "coalition_reformed": len(reformed) >= required_multiplicity,
                             "note": "capability fallback replay; evidence/answer quality not asserted"})
    result = {"contract": "log2026.selector_robustness.v1", "cost_sensitivity": sensitivity,
              "dropout": {"cases": len(dropout_rows), "reformation_rate": mean(row["coalition_reformed"] for row in dropout_rows) if dropout_rows else None,
                          "rows": dropout_rows},
              "claim_boundary": "Routing-level replay only. A reformed coalition is not counted as answer success without evidence evaluation."}
    OUT.mkdir(parents=True, exist_ok=True); (OUT / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = ["# Selector Robustness", "", f"- Coalition dropout cases: {len(dropout_rows)}", f"- Capability-level reformation rate: {result['dropout']['reformation_rate']:.3f}", "",
             "| Cost weight | Best policy |", "|---:|---|"] + [f"| {row['cost_weight']:.3f} | {row['best_policy']} |" for row in sensitivity]
    (OUT / "analysis.md").write_text("\n".join(lines) + "\n"); print(OUT / "analysis.json"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
