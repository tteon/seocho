#!/usr/bin/env python3
"""Codex-runtime revision of paired FinDER slots into integrative benchmark items."""
from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_integrative_candidates.json"


def parse(raw: str) -> dict[str, Any]:
    matches = re.findall(r"\{.*\}", raw, re.S)
    for value in reversed(matches):
        try: return json.loads(value, strict=False)
        except json.JSONDecodeError: continue
    return {"decision": "parse_error", "rationale": raw}


def revise(packet: dict[str, Any]) -> dict[str, Any]:
    prompt = """You are revising a financial graph multi-agent benchmark. Use only facts explicitly present in the two supplied FinDER gold answers. Remove generic forecasts, causal claims, and boilerplate not anchored by issuer-specific facts. Construct one financially natural decision query whose complete response requires one atomic fact from each category view and an explicit conservative reconciliation. A valid reconciliation may conclude that the two facts cannot establish causality; it must still explain their joint decision relevance. Do not merely concatenate the original questions. Reject the item if two valid atomic slots or a natural joint decision cannot be formed. Return JSON only with keys decision (accept/reject), revised_question, slot_1_atomic_gold, slot_2_atomic_gold, cross_view_gold, why_both_views_required, why_single_view_insufficient, removed_unsupported_claims, rationale."""
    command = ["codex", "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "--color", "never", "--cd", str(ROOT), "-"]
    result = subprocess.run(command, input=prompt + "\n" + json.dumps(packet, ensure_ascii=False), text=True, capture_output=True, timeout=600, check=False)
    if result.returncode: return {"decision": "runtime_error", "rationale": result.stderr[-3000:]}
    return parse(result.stdout)


def main() -> int:
    candidates = {row["candidate_id"]: row for row in json.loads((BASE / "candidates.json").read_text())["candidates"]}
    accepted_ids = [row["candidate_id"] for row in json.loads((BASE / "heldout_author_adjudication.json").read_text())["rows"] if row["decision"] == "accept"]
    completed = {row["candidate_id"]: row for row in json.loads(OUT.read_text()).get("rows", [])} if OUT.exists() else {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        for cid in accepted_ids:
            if cid in completed: continue
            row = candidates[cid]; packet = {"issuer": row["issuer"], "categories": row["required_categories"],
                "question_1": row["component_questions"][0], "gold_1": row["required_gold_slots"][0],
                "question_2": row["component_questions"][1], "gold_2": row["required_gold_slots"][1],
                "shared_decision_axes": row["shared_decision_axes"]}
            jobs[pool.submit(revise, packet)] = cid
        for future in concurrent.futures.as_completed(jobs):
            cid = jobs[future]
            try: revision = future.result()
            except Exception as exc: revision = {"decision": "runtime_error", "rationale": f"{type(exc).__name__}: {exc}"}
            completed[cid] = {"candidate_id": cid, "source_component_case_ids": candidates[cid]["component_case_ids"],
                              "issuer": candidates[cid]["issuer"], "categories": candidates[cid]["required_categories"], "revision": revision}
            OUT.write_text(json.dumps({"rows": list(completed.values())}, indent=2, ensure_ascii=False) + "\n")
    counts = {decision: sum(row["revision"].get("decision") == decision for row in completed.values()) for decision in ("accept", "reject", "parse_error", "runtime_error")}
    payload = {"contract": "log2026.revised_integrative_candidates.v1", "source_candidates": len(accepted_ids),
               "construction_uses_source_gold": True, "construction_uses_prior_answer_outputs": False,
               "status": "model-constructed; requires blinded validation before evaluation", "decision_counts": counts,
               "rows": list(completed.values())}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(counts, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
