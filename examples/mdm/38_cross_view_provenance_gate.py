#!/usr/bin/env python3
"""Audit gold provenance and opposite-view leakage in the full FinDER pool."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from statistics import median
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat"
STOP={"about","after","also","and","are","based","been","being","between","both","company","could","data","during","from","have","into","million","that","their","there","these","they","this","through","total","which","with","would"}

def tokens(value:Any)->set[str]:
    return {t for t in re.findall(r"[a-z0-9]+",str(value).lower()) if len(t)>=4 and t not in STOP and not t.isdigit()}

def numbers(value:Any)->set[str]:
    found=set()
    for raw in re.findall(r"(?<![A-Za-z])[-+]?\$?\d[\d,]*(?:\.\d+)?%?",str(value)):
        item=raw.lower().replace("$","").replace(",","").rstrip("%")
        try:item=f"{float(item):.8f}".rstrip("0").rstrip(".")
        except ValueError:pass
        found.add(item)
    return found

def recall(need:set[str],source:set[str])->float|None:
    return len(need&source)/len(need) if need else None

def slot_metrics(gold:str,own_refs:list[str],opposite_refs:list[str])->dict[str,Any]:
    own="\n".join(own_refs);opposite="\n".join(opposite_refs);gt=tokens(gold);gn=numbers(gold)
    return {"gold_token_count":len(gt),"gold_number_count":len(gn),"own_token_recall":recall(gt,tokens(own)),"opposite_token_recall":recall(gt,tokens(opposite)),"own_number_recall":recall(gn,numbers(own)),"opposite_number_recall":recall(gn,numbers(opposite))}

def load_cases()->dict[str,dict[str,Any]]:
    path=ROOT/"examples/mdm/11_index_providers.py";spec=importlib.util.spec_from_file_location("finder_index",path);assert spec and spec.loader
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return {row["case_id"]:row for row in module.load_cases_full(seed=42)}

def qualifies(metrics:list[dict[str,Any]])->bool:
    for row in metrics:
        if row["own_token_recall"] is None or row["own_token_recall"]<0.20:return False
        if row["gold_number_count"] and (row["own_number_recall"] or 0)<0.50:return False
        if (row["opposite_token_recall"] or 0)>=row["own_token_recall"]:return False
        if row["gold_number_count"] and row["opposite_number_recall"]==1:return False
    return True

def audit(pool:dict[str,Any],cases:dict[str,dict[str,Any]])->dict[str,Any]:
    rows=[]
    for candidate in pool["candidates"]:
        left,right=(cases[cid] for cid in candidate["component_case_ids"]);metrics=[slot_metrics(candidate["required_gold_slots"][0],left["references"],right["references"]),slot_metrics(candidate["required_gold_slots"][1],right["references"],left["references"])]
        rows.append({"candidate_id":candidate["candidate_id"],"issuer":candidate["issuer"],"split":candidate["split"],"required_categories":candidate["required_categories"],"shared_decision_axes":candidate["shared_decision_axes"],"slot_metrics":metrics,"automatic_provenance_pass":qualifies(metrics),"human_validation_status":"pending"})
    def summary(split:str)->dict[str,Any]:
        selected=[row for row in rows if row["split"]==split];slots=[slot for row in selected for slot in row["slot_metrics"]]
        return {"candidates":len(selected),"automatic_pass":sum(row["automatic_provenance_pass"] for row in selected),"median_own_token_recall":median(slot["own_token_recall"] for slot in slots),"median_opposite_token_recall":median(slot["opposite_token_recall"] for slot in slots),"median_own_number_recall":median(slot["own_number_recall"] for slot in slots if slot["own_number_recall"] is not None),"median_opposite_number_recall":median(slot["opposite_number_recall"] for slot in slots if slot["opposite_number_recall"] is not None)}
    return {"contract":"log2026.cross_view_provenance_gate.v1","method":"deterministic gold-to-reference support and opposite-view leakage; no model outputs","thresholds":{"own_token_recall_min":0.20,"own_number_recall_min_when_present":0.50,"opposite_token_recall":"strictly below own","opposite_number_recall":"must be below 1.0"},"development":summary("development"),"held_out":summary("held_out"),"rows":rows}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--pool",type=Path,default=BASE/"log2026-full-finder-cross-view-v1/candidates.json");parser.add_argument("--output",type=Path,default=BASE/"log2026-full-finder-cross-view-v1/provenance_gate.json");args=parser.parse_args();payload=audit(json.loads(args.pool.read_text()),load_cases());args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(json.dumps({"development":payload["development"],"held_out":payload["held_out"]},indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
