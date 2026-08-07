#!/usr/bin/env python3
"""Build an evidence-eligible SDCR verification set without answer scores."""
from __future__ import annotations
import argparse,copy,json,re
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat"

def mutate(value:str)->str:
    match=re.search(r"-?\d[\d,]*(?:\.\d+)?",value)
    if not match:return value+" [CONFLICT]"
    number=float(match.group().replace(",",""));replacement=f"{number*1.1:.2f}".rstrip("0").rstrip(".")
    return value[:match.start()]+replacement+value[match.end():]

def build(records:list[dict[str,Any]])->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    accepted=[];rejected=[]
    for record in sorted(records,key=lambda row:row["case_id"]):
        facts=(record.get("survivorship") or {}).get("golden") or []
        structured=[fact for fact in facts if all(str(fact.get(key,"")).strip() for key in ("metric","period","value"))]
        if not structured:
            rejected.append({"case_id":record["case_id"],"reason":"no_metric_period_value_fact"});continue
        eligible=[fact for fact in structured if str(fact.get("basis","")).strip()]
        if not eligible:
            rejected.append({"case_id":record["case_id"],"reason":"comparison_basis_missing"});continue
        original=copy.deepcopy(sorted(eligible,key=lambda fact:(str(fact.get("metric")),str(fact.get("period")),str(fact.get("basis"))))[0]);providers=sorted(set(record.get("effective_selected_providers") or record.get("selected_providers") or []));target=next((provider for provider in providers if provider!=original.get("source")),None)
        if target is None:
            rejected.append({"case_id":record["case_id"],"reason":"no_independent_target_provider"});continue
        poisoned=copy.deepcopy(original);poisoned["value"]=mutate(str(original["value"]));poisoned["source"]=target;marker=f"SDCR_POISON_{record['case_id']}"
        comparable=all(original.get(key,"")==poisoned.get(key,"") for key in ("metric","period","basis"));conflict=comparable and original["value"]!=poisoned["value"]
        accepted.append({"query_id":f"verification-{record['case_id']}","case_id":record["case_id"],"category":record["category"],"selection_uses_answer_scores":False,"original_fact":original,"poisoned_fact":poisoned,"target_provider":target,"synthetic_marker":marker,"comparable":comparable,"conflict_detected":conflict,"expected_mode":"verification_coalition"})
    return accepted,rejected

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=BASE/"log2026-sdcr-verification-v1");args=ap.parse_args();records=json.loads((BASE/"fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())["records"];accepted,rejected=build(records);payload={"contract":"log2026.sdcr_verification_set.v1","source_cases":len(records),"selection":"all cases with a structured metric+period+value fact and an independent selected provider; lexical fact tie-break; no answer scores","accepted":len(accepted),"rejected":len(rejected),"conflict_recall":sum(row["conflict_detected"] for row in accepted)/len(accepted) if accepted else None,"cases":accepted,"rejections":rejected};args.output.mkdir(parents=True,exist_ok=True);(args.output/"verification.json").write_text(json.dumps(payload,indent=2)+"\n")
    lines=["# SDCR Evidence-Eligible Verification Set","",f"- Source cases: {len(records)}",f"- Accepted: {len(accepted)}",f"- Rejected: {len(rejected)}",f"- Deterministic comparable-conflict recall: {payload['conflict_recall']}","", "Rejected cases remain in the artifact with reasons. Selection never reads answer scores.",""];(args.output/"verification.md").write_text("\n".join(lines));print(args.output/"verification.json");return 0
if __name__=="__main__":raise SystemExit(main())
