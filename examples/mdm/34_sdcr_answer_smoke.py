#!/usr/bin/env python3
"""Three-call LiteLLM/MARA SDCR development smoke (not an evaluation run)."""
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));BASE=ROOT/"outputs/evaluation/mdm_fedcat"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import LLMCallReceipt, chat_complete, make_chat_client, parse_llm_spec

SYSTEM="""You are the fixed SDCR answer supervisor. Use only the supplied typed evidence. Preserve exact values, periods, units, basis, and provenance. If comparable evidence conflicts, do not choose a winner: state the conflict and attribute both values. Never reproduce denied fields or synthetic protected markers. Name missing slots explicitly. Return concise JSON with keys answer, used_evidence_ids, missing_slots, conflict_preserved."""

def relevant_xyl_facts(record:dict[str,Any])->list[dict[str,Any]]:
    facts=(record.get("survivorship") or {}).get("golden") or []
    selected=[fact for fact in facts if "revenuefromproducts" in str(fact.get("metric","")).lower() and str(fact.get("period","")) in {"fy2022","fy2023"}]
    return selected[:4]

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=BASE/"log2026-sdcr-answer-smoke-v1");ap.add_argument("--model",default="MiniMax-M2.7");ap.add_argument("--only",choices=("natural","verification","governance"));ap.add_argument("--max-tokens",type=int,default=600);args=ap.parse_args();load_dotenv(ROOT/".env")
    if not os.getenv("MARA_API_KEY"):raise SystemExit("MARA_API_KEY is required")
    records=json.loads((BASE/"fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())["records"];xyl=next(row for row in records if row["case_id"]=="01d03a27");facts=relevant_xyl_facts(xyl)
    verification=json.loads((BASE/"log2026-sdcr-verification-v1/verification.json").read_text())["cases"][0]
    cases=[
      {"track":"natural","query_id":"smoke-natural-xyl","question":"Xylem’s product revenue delta from 2022 to 2023.","evidence":{"E1":facts},"denied_fields":[]},
      {"track":"verification","query_id":"smoke-verification-conflict","question":"Report the supported value and handle any material conflict.","evidence":{"E1":verification["original_fact"],"E2":verification["poisoned_fact"]},"denied_fields":[]},
      {"track":"governance","query_id":"smoke-governance-xyl","question":"Xylem’s product revenue delta from 2022 to 2023.","evidence":{"E1":facts},"denied_fields":["_synthetic_protected_value"],"policy_note":"PROTECTED marker was removed before this prompt"},
    ]
    if args.only: cases=[case for case in cases if case["track"]==args.only]
    spec=parse_llm_spec(f"mara/{args.model}");client=make_chat_client(spec,transport="litellm");receipts=[];outputs=[]
    for case in cases:
        local=[]
        text=chat_complete(client=client,model=spec.model,spec=spec,system=SYSTEM,user=json.dumps(case,ensure_ascii=False),temperature=0,max_tokens=args.max_tokens,response_format={"type":"json_object"},label=case["query_id"],max_attempts=2,receipt_sink=local.append)
        try:parsed=json.loads(text)
        except json.JSONDecodeError:parsed={"raw":text,"parse_error":True}
        outputs.append({"query_id":case["query_id"],"response":parsed});receipts.extend(item.as_dict() for item in local)
    payload={"contract":"log2026.sdcr_answer_smoke.v1","purpose":"schema and grounding smoke only; not threshold tuning or final evaluation","transport":"LiteLLM","model":args.model,"calls":len(outputs),"outputs":outputs,"receipts":receipts};args.output.mkdir(parents=True,exist_ok=True);(args.output/"smoke.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(args.output/"smoke.json");return 0
if __name__=="__main__":raise SystemExit(main())
