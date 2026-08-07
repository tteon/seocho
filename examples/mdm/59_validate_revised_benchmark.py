#!/usr/bin/env python3
"""Independent AGY persona validation of revised integrative candidates."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_blind_validation.json"
spec = importlib.util.spec_from_file_location("agy_panel", ROOT / "examples/mdm/56_agy_persona_panel.py"); assert spec and spec.loader
PANEL = importlib.util.module_from_spec(spec); spec.loader.exec_module(PANEL)


def main() -> int:
    revised = json.loads((BASE / "revised_integrative_candidates.json").read_text())["rows"]
    saved = json.loads(OUT.read_text()) if OUT.exists() else {"reviews": []}
    done = {(row["candidate_id"], row["persona"]) for row in saved["reviews"]}; jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PANEL.PERSONAS)) as pool:
        for row in revised:
            packet = {"issuer": row["issuer"], "categories": row["categories"], **row["revision"]}
            for persona, (model, instruction) in PANEL.PERSONAS.items():
                if (row["candidate_id"], persona) in done: continue
                prompt = f"{instruction}\nYou are blind to the constructor, previous answers, retrieval scores, and author decisions. Validate the revised item. Reject if facts are not atomic, the joint conclusion adds unsupported causality, the query merely concatenates tasks, or one view can complete the requested decision. Return JSON only with keys decision (accept/reject/uncertain), both_views_required (yes/no/uncertain), financially_natural (yes/no/uncertain), atomic_gold_valid (yes/no/uncertain), cross_view_gold_supported (yes/no/uncertain), strongest_objection, rationale.\nITEM:\n{json.dumps(packet, ensure_ascii=False)}"
                jobs.append((row["candidate_id"], persona, model, pool.submit(PANEL.agy, model, prompt)))
        for cid, persona, model, future in jobs:
            try: review = future.result()
            except Exception as exc: review = {"decision": "runtime_error", "rationale": f"{type(exc).__name__}: {exc}"}
            saved["reviews"].append({"candidate_id": cid, "persona": persona, "model": model, "review": review})
            OUT.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    by_case = {}
    for row in saved["reviews"]: by_case.setdefault(row["candidate_id"], []).append(row["review"])
    decisions = {}
    for cid, reviews in by_case.items():
        accepts = sum(r.get("decision") == "accept" and r.get("atomic_gold_valid") == "yes" and r.get("cross_view_gold_supported") == "yes" for r in reviews)
        decisions[cid] = {"qualified_accepts": accepts, "decision": "accept" if accepts >= 3 else "reject", "review_count": len(reviews)}
    summary = {"candidates": len(by_case), "accepted": sum(x["decision"] == "accept" for x in decisions.values()),
               "rejected": sum(x["decision"] == "reject" for x in decisions.values()),
               "rule": "at least 3 of 5 model-persona reviews accept with atomic_gold_valid=yes and cross_view_gold_supported=yes",
               "personas_are_not_independent_human_samples": True}
    payload = {"contract": "log2026.revised_blind_validation.v1", "summary": summary, "decisions": decisions, "reviews": saved["reviews"]}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
