#!/usr/bin/env python3
"""Zero-cost replay of frozen SDCR natural/conflict/governance frames."""
from __future__ import annotations
import argparse,copy,json,re
from pathlib import Path
from statistics import mean
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat"

def mutate_value(value:str)->str:
    match=re.search(r"-?\d[\d,]*(?:\.\d+)?",value)
    if not match:return value+" [POISONED]"
    raw=match.group().replace(",","");number=float(raw);replacement=f"{number*1.1:.2f}".rstrip("0").rstrip(".")
    return value[:match.start()]+replacement+value[match.end():]

def remove_denied(value:Any,denied:set[str])->Any:
    if isinstance(value,dict):return {k:remove_denied(v,denied) for k,v in value.items() if k not in denied}
    if isinstance(value,list):return [remove_denied(item,denied) for item in value]
    return value

def replay_verification(frame:dict[str,Any],record:dict[str,Any])->dict[str,Any]:
    survivor=(record.get("survivorship") or {}).get("golden") or []
    target=str(frame["intervention"]["target_provider"]).removeprefix("silo-")
    candidates=[fact for fact in survivor if fact.get("source")==target] or [fact for fact in survivor if str(fact.get("agreement","1/1")).split("/")[0] != "1"]
    if not candidates:return {"query_id":frame["query_id"],"eligible":False,"reason":"no_structured_comparable_consensus_fact"}
    original=copy.deepcopy(candidates[0]);poisoned=copy.deepcopy(original);poisoned["value"]=mutate_value(str(original.get("value", "")));poisoned["source"]=target;poisoned["synthetic_marker"]=frame["intervention"]["synthetic_marker"]
    comparable=all(original.get(key,"")==poisoned.get(key,"") for key in ("metric","period","basis"));conflict=comparable and original.get("value")!=poisoned.get("value")
    return {"query_id":frame["query_id"],"eligible":True,"comparable":comparable,"conflict_detected":conflict,"trigger":"material_comparable_fact_conflict" if conflict else "none","original":original,"poisoned":poisoned}

def replay_governance(frame:dict[str,Any],record:dict[str,Any])->dict[str,Any]:
    marker=frame["intervention"]["synthetic_marker"];field=frame["intervention"]["mutation"]["field"];evidence=copy.deepcopy(record);evidence[field]=marker;filtered=remove_denied(evidence,{field});serialized=json.dumps(filtered)
    return {"query_id":frame["query_id"],"field_removed":field not in filtered,"marker_disclosed":marker in serialized,"policy_violation":marker in serialized}

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=BASE/"log2026-sdcr-zero-cost-replay-v1");args=ap.parse_args()
    suite=json.loads((BASE/"log2026-sdcr-query-suite-v1/query_frames.json").read_text());category=json.loads((BASE/"fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())["records"];cat={row["case_id"]:row for row in category};baseline=json.loads((BASE/"fedcat-baseline-80-v1/federation_aggregate.json").read_text())["records"];lanes={(row["case_id"],row["lane"]):row for row in baseline}
    natural=[];verification=[];governance=[]
    for frame in suite["frames"]:
        cid=frame["component_case_ids"][0];record=cat[cid]
        if frame["track"]=="natural_local":
            fixed=lanes[(cid,"silo-minimax25")];broadcast=lanes[(cid,"federation")]
            natural.append({"query_id":frame["query_id"],"case_id":cid,"category":record["category"],"selected_graph_ids":[record["category"]],"mode":"single","selector_missing_slots":record.get("selector") and record["selector"].get("missing_slots",[]) or [],"category_token_f1":record["evaluation"]["token_f1"],"fixed_single_token_f1":fixed["evaluation"]["token_f1"],"broadcast_token_f1":broadcast["evaluation"]["token_f1"],"category_context_chars":record["context_chars"],"fixed_context_chars":fixed["context_chars"],"broadcast_context_chars":broadcast["context_chars"]})
        elif frame["track"]=="verification_conflict":verification.append(replay_verification(frame,record))
        elif frame["track"]=="governance":governance.append(replay_governance(frame,record))
    eligible=[row for row in verification if row["eligible"]];summary={"natural_cases":len(natural),"natural_single_rate":sum(row["mode"]=="single" for row in natural)/len(natural),"natural_mean_token_f1":{"category":round(mean(row["category_token_f1"] for row in natural),4),"best_fixed_minimax25":round(mean(row["fixed_single_token_f1"] for row in natural),4),"broadcast":round(mean(row["broadcast_token_f1"] for row in natural),4)},"natural_mean_context_chars":{"category":round(mean(row["category_context_chars"] for row in natural),1),"best_fixed_minimax25":round(mean(row["fixed_context_chars"] for row in natural),1),"broadcast":round(mean(row["broadcast_context_chars"] for row in natural),1)},"verification_frames":len(verification),"verification_eligible":len(eligible),"verification_conflict_recall":round(sum(row["conflict_detected"] for row in eligible)/len(eligible),4) if eligible else None,"governance_frames":len(governance),"protected_marker_disclosure_rate":round(sum(row["marker_disclosed"] for row in governance)/len(governance),4)}
    payload={"contract":"log2026.sdcr_zero_cost_replay.v1","paid_calls":0,"database_writes":0,"claim_scope":"historical answer replay plus synthetic in-memory interventions; not same-snapshot causal answer evidence","summary":summary,"natural":natural,"verification":verification,"governance":governance};args.output.mkdir(parents=True,exist_ok=True);(args.output/"replay.json").write_text(json.dumps(payload,indent=2)+"\n")
    lines=["# SDCR Zero-Cost Replay","",f"- Natural cases: {summary['natural_cases']}",f"- Natural single rate: {summary['natural_single_rate']:.3f}",f"- Mean token F1: {summary['natural_mean_token_f1']}",f"- Mean context chars: {summary['natural_mean_context_chars']}",f"- Verification eligible: {summary['verification_eligible']}/{summary['verification_frames']}",f"- Eligible conflict recall: {summary['verification_conflict_recall']}",f"- Protected-marker disclosure rate: {summary['protected_marker_disclosure_rate']:.3f}","", "Historical answer lanes are not a causal SDCR comparison. Ineligible conflict cases are retained, never imputed.",""];(args.output/"replay.md").write_text("\n".join(lines));print(args.output/"replay.json");return 0
if __name__=="__main__":raise SystemExit(main())
