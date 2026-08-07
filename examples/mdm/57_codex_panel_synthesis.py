#!/usr/bin/env python3
"""Neutral Codex-runtime synthesis of AGY reciprocal persona reviews."""
from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "codex_panel_synthesis.json"


def parse(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match: return {"decision": "parse_error", "rationale": raw}
    try: return json.loads(match.group(), strict=False)
    except json.JSONDecodeError: return {"decision": "parse_error", "rationale": raw}


def synthesize(prompt: str) -> dict[str, Any]:
    command = ["codex", "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
               "--color", "never", "--cd", str(ROOT), "-"]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=600, check=False)
    if result.returncode: return {"decision": "runtime_error", "rationale": result.stderr[-3000:]}
    return parse(result.stdout)


def main() -> int:
    panel = json.loads((BASE / "agy_persona_panel.json").read_text())
    candidates = {row["candidate_id"]: row for row in json.loads((BASE / "candidates.json").read_text())["candidates"]}
    author = {row["candidate_id"]: row["decision"] for row in json.loads((BASE / "heldout_author_adjudication.json").read_text())["rows"]}
    completed = {row["candidate_id"]: row for row in json.loads(OUT.read_text()).get("rows", [])} if OUT.exists() else {}
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in panel["stage2"]: by_case.setdefault(row["candidate_id"], []).append({"persona": row["persona"], "review": row["review"]})
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for cid, reviews in by_case.items():
            if cid in completed: continue
            candidate = candidates[cid]
            prompt = """Act as a neutral meta-review chair. You are blind to author decisions, retrieval outputs, answer scores, and reviewer identities beyond their declared roles. Resolve the benchmark question, not the majority vote. Check whether both category views are necessary, whether one view could answer by chance, whether the joined financial question is natural, and whether both FinDER gold slots are valid. Explicitly preserve unresolved objections. Return JSON only with keys decision (accept/reject/uncertain), both_views_required (yes/no/uncertain), financially_natural (yes/no/uncertain), gold_slots_valid (yes/no/uncertain), decisive_evidence, unresolved_objections, rationale.\n"""
            packet = {"question_1": candidate["component_questions"][0], "gold_slot_1": candidate["required_gold_slots"][0],
                      "question_2": candidate["component_questions"][1], "gold_slot_2": candidate["required_gold_slots"][1],
                      "reciprocal_persona_reviews": reviews}
            jobs[pool.submit(synthesize, prompt + json.dumps(packet, ensure_ascii=False))] = cid
        for future in concurrent.futures.as_completed(jobs):
            cid = jobs[future]
            try: review = future.result()
            except Exception as exc: review = {"decision": "runtime_error", "rationale": f"{type(exc).__name__}: {exc}"}
            completed[cid] = {"candidate_id": cid, "review": review}
            OUT.write_text(json.dumps({"rows": list(completed.values())}, indent=2, ensure_ascii=False) + "\n")
    usable = [row for row in completed.values() if row["review"].get("decision") in {"accept", "reject", "uncertain"}]
    summary = {"cases": len(completed), "usable": len(usable),
               "decision_counts": {decision: sum(row["review"].get("decision") == decision for row in usable) for decision in ("accept", "reject", "uncertain")},
               "author_agreement_rate": sum(row["review"].get("decision") == author[row["candidate_id"]] for row in usable) / len(usable) if usable else None,
               "scope": "Codex meta-review of model personas; not independent human review"}
    payload = {"contract": "log2026.codex_panel_synthesis.v1", "summary": summary, "rows": list(completed.values())}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
