#!/usr/bin/env python3
"""State the oracle-routing ceiling already implicit in the revised benchmark.

The qualified-view broadcast arm receives exactly the independently validated
required category views. It is therefore an oracle minimal-team ceiling for
retrieval, not an ordinary broadcast policy. This script makes that distinction
explicit and prevents later prose from treating it as a discovered router.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "oracle_routing_ceiling.json"


def main() -> int:
    retrieval = json.loads((BASE / "revised_exact_retrieval.json").read_text())
    routing = json.loads((BASE / "revised_sdcr_routing.json").read_text())
    answers = json.loads((BASE / "revised_answer_analysis.json").read_text())
    summary = retrieval["summary"]
    oracle = summary["qualified_view_broadcast"]
    centralized = summary["centralized_single"]
    actual = summary["sdcr"]
    payload = {
        "contract": "log2026.oracle_routing_ceiling.v1",
        "scope": "13 output-blind, persona-screened revised cases; not independent human ground truth",
        "oracle_definition": "qualified_view_broadcast receives both required categories from the validated case label, with the same 2,048-token cap and evidence order as every retrieval arm",
        "actual_router": routing["summary"],
        "retrieval_ceiling": {
            "oracle_minimal_team": oracle,
            "centralized_single": centralized,
            "actual_sdcr": actual,
            "oracle_minus_centralized_slot_token_recall": round(oracle["mean_slot_token_recall"] - centralized["mean_slot_token_recall"], 6),
            "oracle_minus_actual_sdcr_slot_token_recall": round(oracle["mean_slot_token_recall"] - actual["mean_slot_token_recall"], 6),
        },
        "answer_ceiling": {
            "oracle_arm_name": "qualified_view_broadcast",
            "per_model_broadcast_minus_centralized": {
                model: values["broadcast_minus_centralized_slot_macro_f1"]
                for model, values in answers["results"].items()
            },
        },
        "decision": {
            "oracle_answer_advantage_confirmed": False,
            "reason": "Broadcast-minus-centralized slot-F1 intervals contain zero for all answer models; the present 13-case set cannot support an answer-improvement claim.",
            "next_requirement": "Repeat this ceiling on independently human-labeled mixed questions before optimizing the router.",
        },
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(payload["retrieval_ceiling"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
