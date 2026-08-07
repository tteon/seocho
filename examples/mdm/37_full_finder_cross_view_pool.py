#!/usr/bin/env python3
"""Build an output-blind, issuer-disjoint cross-view pool from all FinDER cases."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"outputs/evaluation/mdm_fedcat"
EXCLUDED={"ASC","GAAP","SEC","EBITDA","US","FY","CEO","CFO","ERM","CISO","IT"}
AXES={
 "liquidity_capital_allocation":r"liquid|cash|capital alloc|repurchas|buyback|dividend|debt|borrow|working capital|contract asset|contract liabil",
 "enterprise_risk":r"risk|cyber|security|litig|legal|settle|judg|regulat|contingen|qui tam|disruption",
 "profitability_growth":r"profit|growth|margin|earnings|eps|revenue|cost of sales|performance",
 "governance_audit":r"govern|board|audit|oversight|control|compliance|ciso|erm",
}

def load_cases()->list[dict[str,Any]]:
    path=ROOT/"examples/mdm/11_index_providers.py";spec=importlib.util.spec_from_file_location("finder_index",path);assert spec and spec.loader
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.load_cases_full(seed=42)

def infer_issuer(question:str)->str:
    tokens=re.findall(r"\b[A-Z]{2,5}\b",question)
    valid=[token for token in tokens if token not in EXCLUDED]
    return valid[-1] if valid else ""

def decision_axes(question:str)->set[str]:
    return {axis for axis,pattern in AXES.items() if re.search(pattern,question,re.I)}

def stable_fraction(value:str)->float:
    return int(hashlib.sha256(value.encode()).hexdigest()[:8],16)/0xFFFFFFFF

def build_pool(cases:list[dict[str,Any]],limit:int=240)->dict[str,Any]:
    grouped:dict[str,list[dict[str,Any]]]={}
    for case in cases:
        issuer=infer_issuer(str(case["query"]));axes=decision_axes(str(case["query"]))
        if issuer and axes:grouped.setdefault(issuer,[]).append({**case,"decision_axes":sorted(axes)})
    candidates=[]
    for issuer,rows in sorted(grouped.items()):
        best:dict[tuple[str,str],dict[str,Any]]={}
        for left,right in combinations(sorted(rows,key=lambda row:str(row["case_id"])),2):
            if left["category"]==right["category"]:continue
            shared=sorted(set(left["decision_axes"])&set(right["decision_axes"]))
            if not shared:continue
            categories=tuple(sorted((str(left["category"]),str(right["category"]))))
            score=len(shared)*10+len(set(left["decision_axes"])|set(right["decision_axes"]))
            cid=f"{issuer.lower()}-{left['case_id']}-{right['case_id']}"
            row={"candidate_id":cid,"issuer":issuer,"required_categories":[left["category"],right["category"]],"component_case_ids":[left["case_id"],right["case_id"]],"component_questions":[left["query"],right["query"]],"required_gold_slots":[left["expected_answer"],right["expected_answer"]],"shared_decision_axes":shared,"score":score,"selection_uses_model_outputs":False,"human_validation_status":"pending","split":"development" if stable_fraction(issuer)<0.25 else "held_out"}
            if categories not in best or (score,cid)>(best[categories]["score"],best[categories]["candidate_id"]):best[categories]=row
        candidates.extend(best.values())
    candidates=sorted(candidates,key=lambda row:(-row["score"],stable_fraction(row["candidate_id"]),row["candidate_id"]))[:limit]
    return {"contract":"log2026.full_finder_cross_view_pool.v1","source_cases":len(cases),"selection":"output-blind shared decision-axis; at most one pair per issuer/category pair","issuer_disjoint_split":True,"candidate_count":len(candidates),"development_count":sum(row["split"]=="development" for row in candidates),"held_out_count":sum(row["split"]=="held_out" for row in candidates),"candidates":candidates}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--limit",type=int,default=240);parser.add_argument("--output",type=Path,default=BASE/"log2026-full-finder-cross-view-v1");args=parser.parse_args();payload=build_pool(load_cases(),args.limit);args.output.mkdir(parents=True,exist_ok=True);(args.output/"candidates.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")
    lines=["# Full FinDER Cross-View Candidate Pool","",f"- Source cases: {payload['source_cases']}",f"- Candidates: {payload['candidate_count']}",f"- Development: {payload['development_count']}",f"- Held out: {payload['held_out_count']}","","Selection uses questions, categories, and issuer identifiers only; no model output or evaluation score.",""];(args.output/"README.md").write_text("\n".join(lines));print(args.output/"candidates.json");return 0
if __name__=="__main__":raise SystemExit(main())
