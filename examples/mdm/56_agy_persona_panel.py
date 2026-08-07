#!/usr/bin/env python3
"""AGY persona critique and reciprocal revision for blind-review audit cases."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "agy_persona_panel.json"
PERSONAS = {
    "finance": ("Gemini 3.5 Flash (High)", "Act as a financial disclosure domain reviewer. Focus on whether the joined decision need is natural and whether both gold slots are financially valid."),
    "graph_mas": ("Gemini 3.1 Pro (High)", "Act as a graph and multi-agent systems reviewer. Focus on whether separate category views are genuinely necessary rather than merely available."),
    "benchmark": ("Claude Sonnet 4.6 (Thinking)", "Act as a benchmark and statistics reviewer. Focus on leakage, construct validity, single-view sufficiency, and whether the proposed gold supports the claim."),
    "governance": ("GPT-OSS 120B (Medium)", "Act as an adversarial data-governance reviewer. Look for issuer contamination, unsafe evidence federation, ambiguity, and reasons to abstain or reject."),
    "audit": ("Claude Sonnet 4.6 (Thinking)", "Act as a financial-statement auditor. Check whether each requested answer field is defined at the correct reporting period, unit, issuer, and accounting scope; flag unsupported joins and material ambiguity."),
}
SCHEMA = "Return JSON only with keys persona, both_views_required (yes/no/uncertain), single_view_sufficient (yes/no/uncertain), financially_natural (yes/no/uncertain), gold_slots_valid (yes/no/uncertain), decision (accept/reject/uncertain), strongest_support, strongest_objection, rationale."


def parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip(); text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try: return json.loads(text, strict=False)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try: return json.loads(match.group(), strict=False)
            except json.JSONDecodeError: pass
        return {"decision": "parse_error", "rationale": raw}


def agy(model: str, prompt: str) -> dict[str, Any]:
    command = ["agy", "--print", prompt, "--model", model, "--sandbox", "--print-timeout", "5m"]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=330, check=False)
    if result.returncode: return {"decision": "runtime_error", "rationale": result.stderr[-2000:], "returncode": result.returncode}
    return parse_json(result.stdout)


def main() -> int:
    blind = json.loads((BASE / "blind_llm_review.json").read_text())
    candidates = {row["candidate_id"]: row for row in json.loads((BASE / "candidates.json").read_text())["candidates"]}
    incomplete = {row["candidate_id"] for row in blind["reviews"] if row["review"].get("decision") not in {"accept", "reject"}}
    disputed = {cid for cid, decision in blind["consensus"].items() if decision == "disagreement"} | incomplete
    agreed = sorted((cid for cid, decision in blind["consensus"].items() if decision != "disagreement"),
                    key=lambda cid: hashlib.sha256(("audit:" + cid).encode()).hexdigest())[:5]
    targets = sorted(disputed) + agreed
    saved = json.loads(OUT.read_text()) if OUT.exists() else {"stage1": [], "stage2": []}
    stage1_keys = {(row["candidate_id"], row["persona"]) for row in saved["stage1"]}
    jobs = []
    for cid in targets:
        row = candidates[cid]
        packet = {"blind_id": "P" + hashlib.sha256(cid.encode()).hexdigest()[:10], "question_1": row["component_questions"][0],
                  "gold_slot_1": row["required_gold_slots"][0], "question_2": row["component_questions"][1],
                  "gold_slot_2": row["required_gold_slots"][1], "proposed_join": "Answer both requirements for the same issuer."}
        for persona, (model, instruction) in PERSONAS.items():
            if (cid, persona) not in stage1_keys:
                prompt = f"{instruction}\nYou are blinded to all other reviewers, model answers, scores, and author decisions.\n{SCHEMA}\nCASE:\n{json.dumps(packet, ensure_ascii=False)}"
                jobs.append((cid, persona, model, prompt))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PERSONAS)) as pool:
        futures = {pool.submit(agy, model, prompt): (cid, persona, model) for cid, persona, model, prompt in jobs}
        for future in concurrent.futures.as_completed(futures):
            cid, persona, model = futures[future]
            try: review = future.result()
            except Exception as exc: review = {"decision": "runtime_error", "rationale": f"{type(exc).__name__}: {exc}"}
            saved["stage1"].append({"candidate_id": cid, "persona": persona, "model": model, "review": review})
            OUT.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in saved["stage1"]: by_case.setdefault(row["candidate_id"], []).append(row)
    stage2_keys = {(row["candidate_id"], row["persona"]) for row in saved["stage2"]}; jobs = []
    for cid in targets:
        candidate = candidates[cid]
        for persona, (model, instruction) in PERSONAS.items():
            if (cid, persona) in stage2_keys: continue
            own = next(row["review"] for row in by_case[cid] if row["persona"] == persona)
            counterpoints = [{"persona": row["persona"], "strongest_support": row["review"].get("strongest_support"),
                              "strongest_objection": row["review"].get("strongest_objection"), "rationale": row["review"].get("rationale")}
                             for row in by_case[cid] if row["persona"] != persona]
            prompt = f"{instruction}\nRe-evaluate your blinded review after reading anonymous peer evidence. Peer final decisions are intentionally hidden. Do not defer to consensus.\n{SCHEMA}\nYOUR INITIAL REVIEW:\n{json.dumps(own, ensure_ascii=False)}\nPEER EVIDENCE:\n{json.dumps(counterpoints, ensure_ascii=False)}\nCASE:\n{json.dumps({'question_1':candidate['component_questions'][0],'gold_slot_1':candidate['required_gold_slots'][0],'question_2':candidate['component_questions'][1],'gold_slot_2':candidate['required_gold_slots'][1]}, ensure_ascii=False)}"
            jobs.append((cid, persona, model, prompt))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PERSONAS)) as pool:
        futures = {pool.submit(agy, model, prompt): (cid, persona, model) for cid, persona, model, prompt in jobs}
        for future in concurrent.futures.as_completed(futures):
            cid, persona, model = futures[future]
            try: review = future.result()
            except Exception as exc: review = {"decision": "runtime_error", "rationale": f"{type(exc).__name__}: {exc}"}
            saved["stage2"].append({"candidate_id": cid, "persona": persona, "model": model, "review": review})
            OUT.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    changes = 0; usable = 0
    first = {(r["candidate_id"], r["persona"]): r["review"].get("decision") for r in saved["stage1"]}
    for row in saved["stage2"]:
        decision = row["review"].get("decision")
        if decision in {"accept", "reject", "uncertain"}:
            usable += 1; changes += decision != first[(row["candidate_id"], row["persona"])]
    saved.update({"contract": "log2026.agy_persona_panel.v2", "selection": {"disputed_or_incomplete": sorted(disputed), "agreed_audit_sample": agreed},
                  "personas_are_not_independent_samples": True, "summary": {"cases": len(targets), "stage1_reviews": len(saved["stage1"]),
                  "stage2_reviews": len(saved["stage2"]), "usable_stage2": usable, "decision_change_rate": changes / usable if usable else None}})
    OUT.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(saved["summary"], indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
