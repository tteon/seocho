#!/usr/bin/env python3
"""LiteLLM/MARA slot routing for blind-validated revised benchmark items."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_sdcr_routing.json"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import chat_complete, make_chat_client, parse_llm_spec

CATEGORIES = ["Accounting", "Company overview", "Financials", "Footnotes", "Governance", "Legal", "Risk", "Shareholder return"]
SYSTEM = """You are the SDCR slot router. Your first output character must be { and you must output one JSON object only, with no reasoning before or after it. Decompose the financial decision question into the smallest evidence slots and select the smallest set of category graph agents that can fill them. Use only these exact category names: Accounting, Company overview, Financials, Footnotes, Governance, Legal, Risk, Shareholder return. Do not answer the question and do not see gold answers. JSON keys: required_slots (list of objects with slot and category), selected_categories (deduplicated list), action (single or complementary_coalition), rationale. Select multiple agents only when distinct evidence slots require them."""


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not os.getenv("MARA_API_KEY"): raise SystemExit("MARA_API_KEY missing")
    revisions = {row["candidate_id"]: row for row in json.loads((BASE / "revised_integrative_candidates.json").read_text())["rows"]}
    validation = json.loads((BASE / "revised_blind_validation.json").read_text())["decisions"]
    ids = [cid for cid, value in validation.items() if value["decision"] == "accept"]
    prior = json.loads(OUT.read_text()).get("rows", []) if OUT.exists() else []
    failed_attempts = [row for row in prior if row.get("router", {}).get("action") == "parse_error"]
    saved = [row for row in prior if row.get("router", {}).get("action") != "parse_error"]
    completed = {row["candidate_id"] for row in saved}
    spec = parse_llm_spec("mara/MiniMax-M2.7"); client = make_chat_client(spec, transport="litellm")
    for cid in ids:
        if cid in completed: continue
        row = revisions[cid]; receipts = []
        raw = chat_complete(client=client, model=spec.model, spec=spec, system=SYSTEM,
                            user=json.dumps({"question": row["revision"]["revised_question"], "available_categories": CATEGORIES}, ensure_ascii=False),
                            temperature=0, max_tokens=900, response_format={"type": "json_object"}, label=f"revised-route-{cid}-json-retry",
                            max_attempts=2, receipt_sink=receipts.append)
        try: result = json.loads(raw)
        except json.JSONDecodeError: result = {"action": "parse_error", "raw": raw}
        selected = [c for c in result.get("selected_categories", []) if c in CATEGORIES]
        expected = set(row["categories"]); covered = expected <= set(selected)
        saved.append({"candidate_id": cid, "selected_categories": selected, "expected_categories": row["categories"],
                      "required_view_coverage": len(expected & set(selected)) / len(expected), "both_required_views_covered": covered,
                      "router": result, "receipt": [receipt.as_dict() for receipt in receipts]})
        OUT.write_text(json.dumps({"rows": saved}, indent=2, ensure_ascii=False) + "\n")
    summary = {"cases": len(saved), "both_view_route_accuracy": sum(row["both_required_views_covered"] for row in saved) / len(saved),
               "mean_required_view_coverage": sum(row["required_view_coverage"] for row in saved) / len(saved),
               "mean_selected_agents": sum(len(row["selected_categories"]) for row in saved) / len(saved),
               "failed_case_policy": "intention-to-treat zero for SDCR coalition if both required views are not selected"}
    OUT.write_text(json.dumps({"contract": "log2026.revised_sdcr_routing.v1", "summary": summary,
                               "excluded_serialization_failures": failed_attempts, "rows": saved}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
