#!/usr/bin/env python3
"""Multi-model answer evaluation on the blind-validated revised benchmark."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_answers.json"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import chat_complete, make_chat_client, parse_llm_spec

SYSTEM = """Your first output character must be { and you must output JSON only. Answer the financial decision question using only supplied graph evidence. Fill both atomic evidence slots, then give a conservative cross-view conclusion. Do not infer causality that the evidence does not establish. If a slot is missing, say unsupported. JSON keys: answer, slot_1_answer, slot_2_answer, cross_view_conclusion, slot_1_supported, slot_2_supported, used_evidence_ids, missing_slots."""
STOP = {"the", "and", "for", "from", "that", "this", "with", "does", "not"}


def words(value: str) -> set[str]: return {t for t in re.findall(r"[a-z0-9]+", value.lower()) if len(t) > 2 and t not in STOP}
def nums(value: str) -> set[str]: return {x.replace(",", "").replace("$", "").rstrip("%") for x in re.findall(r"\$?-?\d[\d,]*(?:\.\d+)?%?", value)}
def f1(pred: str, gold: str) -> float:
    p, g = words(pred), words(gold)
    if not p or not g: return 0.0
    overlap = len(p & g); precision, recall = overlap / len(p), overlap / len(g)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
def serialize(nodes): return json.dumps([{"evidence_id": f"E{i+1}", "labels": n["labels"], "properties": n["props"]} for i, n in enumerate(nodes)], ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not os.getenv("MARA_API_KEY"): raise SystemExit("MARA_API_KEY missing")
    retrieval = json.loads((BASE / "revised_exact_retrieval.json").read_text())["rows"]
    model_arms = {"MiniMax-M2.7": ["left_single", "right_single", "centralized_single", "qualified_view_broadcast", "category_only", "slot_only", "sdcr"],
                  "DeepSeek-V3.1": ["centralized_single", "qualified_view_broadcast", "sdcr"],
                  "gpt-oss-120b": ["centralized_single", "qualified_view_broadcast", "sdcr"]}
    prior = json.loads(OUT.read_text()).get("rows", []) if OUT.exists() else []; completed = {(r["candidate_id"], r["model"], r["arm"]) for r in prior}; rows = prior
    clients = {}
    for model in model_arms:
        spec = parse_llm_spec("mara/" + model); clients[model] = (spec, make_chat_client(spec, transport="litellm"))
    for item in retrieval:
        for model, arms in model_arms.items():
            spec, client = clients[model]
            for arm in arms:
                key = (item["candidate_id"], model, arm)
                if key in completed: continue
                evidence_nodes = item["arms"][arm]["evidence"]
                if arm == "sdcr" and not evidence_nodes:
                    rows.append({"candidate_id": item["candidate_id"], "issuer": item["issuer"], "model": model, "arm": arm,
                                 "response": {"answer": "", "routing_failure": True}, "slot_macro_f1": 0.0, "numeric_recall": 0.0,
                                 "cross_view_f1": 0.0, "unsupported_numeric_rate": 0.0, "latency_seconds": 0.0, "receipt": []})
                    OUT.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False) + "\n"); continue
                receipts = []; started = time.perf_counter()
                raw = chat_complete(client=client, model=spec.model, spec=spec, system=SYSTEM,
                    user=json.dumps({"question": item["question"], "evidence": serialize(evidence_nodes)}, ensure_ascii=False),
                    temperature=0, max_tokens=750, response_format={"type": "json_object"}, label=f"revised-answer-{item['candidate_id']}-{model}-{arm}",
                    max_attempts=2, receipt_sink=receipts.append)
                try: response = json.loads(raw)
                except json.JSONDecodeError: response = {"answer": raw, "parse_error": True}
                text = " ".join(str(response.get(key, "")) for key in ("answer", "slot_1_answer", "slot_2_answer", "cross_view_conclusion"))
                slot_scores = [f1(text, gold) for gold in item["golds"][:2]]; gold_numbers = nums(" ".join(item["golds"]))
                evidence_numbers = nums(serialize(evidence_nodes)); answer_numbers = nums(re.sub(r"\bE\d+\b", "", text, flags=re.I))
                rows.append({"candidate_id": item["candidate_id"], "issuer": item["issuer"], "model": model, "arm": arm, "response": response,
                             "slot_macro_f1": mean(slot_scores), "numeric_recall": len(gold_numbers & answer_numbers) / len(gold_numbers) if gold_numbers else 0.0,
                             "cross_view_f1": f1(text, item["golds"][2]),
                             "unsupported_numeric_rate": len(answer_numbers - evidence_numbers) / len(answer_numbers) if answer_numbers else 0.0,
                             "latency_seconds": time.perf_counter() - started, "receipt": [r.as_dict() for r in receipts]})
                OUT.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False) + "\n")
    summary = {}; raw_text_secondary = {}
    for model, arms in model_arms.items():
        summary[model] = {}; raw_text_secondary[model] = {}
        for arm in arms:
            selected = [r for r in rows if r["model"] == model and r["arm"] == arm]
            invalid = [r for r in selected if r["response"].get("parse_error")]
            summary[model][arm] = {
                field: round(mean(0.0 if r["response"].get("parse_error") else r[field] for r in selected), 6)
                for field in ("slot_macro_f1", "numeric_recall", "cross_view_f1", "unsupported_numeric_rate")
            }
            summary[model][arm].update({"latency_seconds": round(mean(r["latency_seconds"] for r in selected), 6),
                                        "schema_failure_rate": round(len(invalid) / len(selected), 6)})
            raw_text_secondary[model][arm] = {field: round(mean(r[field] for r in selected), 6)
                                              for field in ("slot_macro_f1", "numeric_recall", "cross_view_f1")}
    payload = {"contract": "log2026.revised_multi_model_answers.v1", "cases": len(retrieval), "model_arms": model_arms,
               "failure_policy": "routing failure and schema failure receive zero in intention-to-treat",
               "summary": summary, "raw_text_secondary": raw_text_secondary, "rows": rows}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
