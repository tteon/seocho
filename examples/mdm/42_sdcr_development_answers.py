#!/usr/bin/env python3
"""Fixed-answerer development comparison for single views and SDCR coalition."""
from __future__ import annotations
import argparse,json,os,re,sys,time
from pathlib import Path
from statistics import mean
from typing import Any

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));BASE=ROOT/"outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
from dotenv import load_dotenv
from examples.finder.lib.llm_io import chat_complete,make_chat_client,parse_llm_spec

SYSTEM="""You are the fixed graph-evidence answerer. Answer both numbered requirements using only supplied evidence. Preserve values, periods, basis, and provenance. If a slot is unsupported, say unsupported; never invent it. Return JSON with keys answer, slot_1_supported, slot_2_supported, used_evidence_ids, missing_slots."""

def words(value:str)->list[str]:return re.findall(r"[a-z0-9]+",value.lower())
def token_f1(pred:str,gold:str)->float:
    from collections import Counter
    p,g=Counter(words(pred)),Counter(words(gold));overlap=sum((p&g).values())
    if not p or not g:return 0.0
    precision,recall=overlap/sum(p.values()),overlap/sum(g.values());return 2*precision*recall/(precision+recall) if precision+recall else 0.0
def nums(value:str)->set[str]:return {x.replace(",","").replace("$","").rstrip("%") for x in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?",value)}
def number_recall(pred:str,gold:str)->float|None:
    g=nums(gold);return len(g&nums(pred))/len(g) if g else None
def serialize(nodes:list[dict[str,Any]],max_chars:int=24000)->str:
    rows=[{"evidence_id":f"E{i+1}","labels":node["labels"],"properties":node["props"]} for i,node in enumerate(nodes)];return json.dumps(rows,ensure_ascii=False)[:max_chars]

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--model",default="MiniMax-M2.7");parser.add_argument("--output",type=Path,default=BASE/"development_answers.json");parser.add_argument("--retrieval",type=Path,default=BASE/"development_retrieval.json");parser.add_argument("--track",default="development_only");parser.add_argument("--max-tokens",type=int,default=900);parser.add_argument("--retry-parse-errors",action="store_true");args=parser.parse_args();load_dotenv(ROOT/".env");spec=parse_llm_spec(f"mara/{args.model}");client=make_chat_client(spec,transport="litellm");retrieval=json.loads(args.retrieval.read_text());pool=json.loads((BASE/"candidates.json").read_text());by_id={row["candidate_id"]:row for row in pool["candidates"]};completed={}
    if args.output.exists():completed={(row["candidate_id"],row["arm"]):row for row in json.loads(args.output.read_text()).get("rows",[]) if not (args.retry_parse_errors and row.get("response",{}).get("parse_error"))}
    rows=list(completed.values())
    for item in retrieval["rows"]:
      candidate=by_id[item["candidate_id"]];gold="\n".join(candidate["required_gold_slots"])
      question=f"(1) {candidate['component_questions'][0]}\n(2) {candidate['component_questions'][1]}"
      for arm in ("left_single","right_single","sdcr_coalition"):
        if (item["candidate_id"],arm) in completed:continue
        evidence=serialize(item["evidence"][arm]);receipts=[];started=time.perf_counter();raw=chat_complete(client=client,model=spec.model,spec=spec,system=SYSTEM,user=json.dumps({"question":question,"evidence":evidence},ensure_ascii=False),temperature=0,max_tokens=args.max_tokens,response_format={"type":"json_object"},label=f"dev-{item['candidate_id']}-{arm}",max_attempts=2,receipt_sink=receipts.append)
        try:parsed=json.loads(raw)
        except json.JSONDecodeError:parsed={"answer":raw,"parse_error":True}
        if not isinstance(parsed,dict):parsed={"answer":raw,"parse_error":True,"schema_type":type(parsed).__name__}
        answer=str(parsed.get("answer",raw));row={"candidate_id":item["candidate_id"],"arm":arm,"response":parsed,"token_f1":token_f1(answer,gold),"number_recall":number_recall(answer,gold),"latency_seconds":time.perf_counter()-started,"receipt":[r.as_dict() for r in receipts]};rows.append(row);payload={"contract":"log2026.sdcr_answers.v1","model":args.model,"track":args.track,"rows":rows};args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")
    summary={arm:{"cases":sum(row["arm"]==arm for row in rows),"mean_token_f1":mean(row["token_f1"] for row in rows if row["arm"]==arm),"mean_number_recall":mean(row["number_recall"] for row in rows if row["arm"]==arm and row["number_recall"] is not None),"mean_latency_seconds":mean(row["latency_seconds"] for row in rows if row["arm"]==arm)} for arm in ("left_single","right_single","sdcr_coalition")};payload={"contract":"log2026.sdcr_answers.v1","model":args.model,"track":args.track,"summary":summary,"rows":rows};args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(json.dumps(summary,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
