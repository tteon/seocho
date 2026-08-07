#!/usr/bin/env python3
"""Two-model output-blind review of cross-view candidate necessity."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "blind_llm_review.json"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import chat_complete, make_chat_client, parse_llm_spec

SYSTEM = """You are a blinded financial benchmark reviewer. You do not see graph retrieval, model answers, scores, or the author's decision. Judge only the two source questions and their source-gold answer slots. Return JSON: both_views_required (yes/no/uncertain), single_view_sufficient (yes/no/uncertain), financially_natural (yes/no/uncertain), gold_slots_valid (yes/no/uncertain), decision (accept/reject/uncertain), rationale (one concise sentence). Accept only if both views are genuinely needed, the joined question is financially coherent, and both source slots are valid."""
MODELS = ("DeepSeek-V3.1", "gpt-oss-120b")


def blind_id(candidate_id: str) -> str:
    return "B" + hashlib.sha256(("log2026-blind-v1:" + candidate_id).encode()).hexdigest()[:10]


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not os.getenv("MARA_API_KEY"): raise SystemExit("MARA_API_KEY missing")
    candidates = {row["candidate_id"]: row for row in json.loads((BASE / "candidates.json").read_text())["candidates"]}
    author = json.loads((BASE / "heldout_author_adjudication.json").read_text())["rows"]
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    if OUT.exists():
        saved = json.loads(OUT.read_text())
        completed = {(row["candidate_id"], row["reviewer_model"]): row for row in saved.get("reviews", [])}
    reviews = list(completed.values())
    clients = {model: (parse_llm_spec("mara/" + model), None) for model in MODELS}
    clients = {model: (spec, make_chat_client(spec, transport="litellm")) for model, (spec, _) in clients.items()}
    for author_row in author:
        candidate = candidates[author_row["candidate_id"]]
        packet = {"blind_id": blind_id(candidate["candidate_id"]), "question_1": candidate["component_questions"][0],
                  "gold_slot_1": candidate["required_gold_slots"][0], "question_2": candidate["component_questions"][1],
                  "gold_slot_2": candidate["required_gold_slots"][1], "proposed_join": "Answer both requirements for the same issuer."}
        for model in MODELS:
            key = (candidate["candidate_id"], model)
            if key in completed: continue
            spec, client = clients[model]; receipts = []
            raw = chat_complete(client=client, model=spec.model, spec=spec, system=SYSTEM,
                                user=json.dumps(packet, ensure_ascii=False), temperature=0, max_tokens=500,
                                response_format={"type": "json_object"}, label=f"blind-{packet['blind_id']}-{model}",
                                max_attempts=2, receipt_sink=receipts.append)
            try: result = json.loads(raw)
            except json.JSONDecodeError: result = {"decision": "parse_error", "rationale": raw}
            row = {"candidate_id": candidate["candidate_id"], "blind_id": packet["blind_id"], "reviewer_model": model,
                   "review": result, "receipt": [receipt.as_dict() for receipt in receipts]}
            reviews.append(row)
            OUT.write_text(json.dumps({"contract": "log2026.blind_llm_review.v1", "reviews": reviews}, indent=2, ensure_ascii=False) + "\n")
    author_decisions = {row["candidate_id"]: row["decision"] for row in author}
    usable = [row for row in reviews if row["review"].get("decision") in {"accept", "reject"}]
    by_candidate: dict[str, list[str]] = {}
    for row in usable: by_candidate.setdefault(row["candidate_id"], []).append(row["review"]["decision"])
    consensus = {cid: values[0] if len(values) == 2 and len(set(values)) == 1 else "disagreement" for cid, values in by_candidate.items()}
    summary = {"candidates": len(author), "review_models": list(MODELS), "usable_reviews": len(usable),
               "two_reviewer_agreement_rate": sum(value != "disagreement" for value in consensus.values()) / len(consensus) if consensus else 0,
               "consensus_author_agreement_rate": sum(value == author_decisions[cid] for cid, value in consensus.items()) / len(consensus) if consensus else 0,
               "scope": "automated output-blind review, not independent human adjudication"}
    OUT.write_text(json.dumps({"contract": "log2026.blind_llm_review.v1", "summary": summary, "consensus": consensus, "reviews": reviews}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
