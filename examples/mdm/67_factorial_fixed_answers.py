#!/usr/bin/env python3
"""Fixed DeepSeek answerer for the 256-cell factorial mediation gate."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-factorial-mediation-v1"
SOURCE = BASE / "retrieval.json"; OUT = BASE / "answers.json"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import chat_complete, make_chat_client, parse_llm_spec

SYSTEM = "Your first output character must be { and you must output JSON only. Answer the question using only supplied graph evidence. Preserve values and periods; say unsupported when evidence is insufficient. JSON keys: answer, supported, used_evidence_ids."


def words(value): return set(re.findall(r"[a-z0-9]+", str(value).lower()))
def nums(value): return {x.replace(",", "").replace("$", "").rstrip("%") for x in re.findall(r"\$?-?\d[\d,]*(?:\.\d+)?%?", str(value))}
def f1(pred, gold):
    p, g = words(pred), words(gold)
    if not p or not g: return 0.0
    overlap = len(p & g); precision, recall = overlap / len(p), overlap / len(g)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
def serialize(nodes): return json.dumps([{"evidence_id": f"E{i+1}", "labels": n["labels"], "properties": n["props"]} for i,n in enumerate(nodes)], ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not os.getenv("MARA_API_KEY"): raise SystemExit("MARA_API_KEY missing")
    cells = json.loads(SOURCE.read_text())["rows"]; saved = json.loads(OUT.read_text()).get("rows", []) if OUT.exists() else []; done = {r["cell_id"] for r in saved}
    spec = parse_llm_spec("mara/DeepSeek-V3.1"); client = make_chat_client(spec, transport="litellm")
    def call(cell):
        receipts=[]; started=time.perf_counter(); raw=chat_complete(client=client,model=spec.model,spec=spec,system=SYSTEM,
            user=json.dumps({"question":cell["question"],"evidence":serialize(cell["retrieved_nodes"])},ensure_ascii=False),temperature=0,max_tokens=650,
            response_format={"type":"json_object"},label=f"factorial-answer-{cell['cell_id']}",max_attempts=2,receipt_sink=receipts.append)
        try: response=json.loads(raw)
        except json.JSONDecodeError: response={"answer":raw,"parse_error":True}
        answer=str(response.get("answer","")); gn=nums(cell["gold"])
        return {"cell_id":cell["cell_id"],"case_id":cell["case_id"],"prompt_level":cell["prompt_level"],"ontology_level":cell["ontology_level"],
                "provider_id":cell["provider_id"],"nodes_created":cell["nodes_created"],"rels_created":cell["rels_created"],
                "retrieval_token_recall":cell["token_recall"],"retrieval_number_recall":cell["number_recall"],"response":response,
                "answer_token_f1":f1(answer,cell["gold"]),"answer_number_recall":len(gn&nums(answer))/len(gn) if gn else None,
                "latency_seconds":time.perf_counter()-started,"receipt":[r.as_dict() for r in receipts]}
    pending=[cell for cell in cells if cell["cell_id"] not in done]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(call,cell):cell["cell_id"] for cell in pending}
        for future in concurrent.futures.as_completed(futures):
            try: row=future.result()
            except Exception as exc: row={"cell_id":futures[future],"runtime_error":f"{type(exc).__name__}: {exc}"}
            saved.append(row); OUT.write_text(json.dumps({"rows":saved},indent=2,ensure_ascii=False)+"\n")
    valid=[r for r in saved if not r.get("runtime_error")]; summary={"cells":len(saved),"valid":len(valid),"runtime_errors":len(saved)-len(valid),
        "schema_failures":sum(r["response"].get("parse_error",False) for r in valid),
        "itt_answer_token_f1":mean(0 if r["response"].get("parse_error") else r["answer_token_f1"] for r in valid),
        "itt_answer_number_recall":mean(0 if r["response"].get("parse_error") else (r["answer_number_recall"] or 0) for r in valid)}
    OUT.write_text(json.dumps({"contract":"log2026.factorial_fixed_answers.v1","model":"DeepSeek-V3.1","summary":summary,"rows":saved},indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(summary,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
