#!/usr/bin/env python3
"""Freeze the SDCR query/test manifest without answer calls."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"outputs/evaluation/mdm_fedcat"

def accepted_ids(annotations: dict[str,Any])->set[str]:
    return {row["candidate_id"] for row in annotations["annotations"] if isinstance(row.get("adjudicated"),dict) and row["adjudicated"].get("decision")=="accept"}

def build_frames(protocol: dict[str,Any], candidates: dict[str,Any], annotations: dict[str,Any], gold: dict[str,dict[str,Any]])->list[dict[str,Any]]:
    frames=[]
    for case_id in protocol["split"]["test"]:
        row=gold[case_id]
        frames.append({"query_id":f"natural-{case_id}","track":"natural_local","natural":True,"question":row["query"],"component_case_ids":[case_id],"required_slots":["original_finder_answer"],"expected_query_class":"local_view","adjudication_status":"source_gold"})
    accepted=accepted_ids(annotations)
    for row in candidates["candidates"]:
        if row["candidate_id"] not in accepted: continue
        frames.append({"query_id":f"complementary-{row['candidate_id']}","track":"complementary","natural":False,"question":row["paired_question"],"component_case_ids":row["component_case_ids"],"required_slots":["component_answer_1","component_answer_2"],"required_categories":row["required_categories"],"expected_query_class":"cross_view_complementary","adjudication_status":"accepted"})
    seen=set()
    for item in protocol["interventions"]:
        key=(item["case_id"],item["kind"])
        if key in seen: continue
        seen.add(key); row=gold[item["case_id"]]
        track="verification_conflict" if item["kind"]=="one_view_numeric_poison" else "governance"
        frames.append({"query_id":item["intervention_id"],"track":track,"natural":False,"question":row["query"],"component_case_ids":[item["case_id"]],"required_slots":["original_finder_answer"],"expected_query_class":"cross_view_conflicting" if track=="verification_conflict" else "policy_guarded","intervention":item,"adjudication_status":"synthetic_pre_registered"})
    return frames

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=BASE/"log2026-sdcr-query-suite-v1");args=ap.parse_args()
    protocol=json.loads((BASE/"log2026-mas-protocol-v1/protocol.json").read_text());candidates=json.loads((BASE/"log2026-finder-cross-view-v1/candidates.json").read_text());annotations=json.loads((BASE/"log2026-finder-cross-view-v1/annotations.json").read_text())
    import importlib.util
    p=ROOT/"examples/mdm/11_index_providers.py";s=importlib.util.spec_from_file_location("idx",p);assert s and s.loader;m=importlib.util.module_from_spec(s);s.loader.exec_module(m);gold={str(row["case_id"]):row for row in m.load_cases_full(42)}
    frames=build_frames(protocol,candidates,annotations,gold);counts={track:sum(row["track"]==track for row in frames) for track in sorted({row["track"] for row in frames})}
    payload={"contract":"log2026.sdcr_query_suite.v1","frozen":True,"counts":counts,"pending_complementary_candidates":len(candidates["candidates"])-len(accepted_ids(annotations)),"retrieval_contract":{"entity_seeded_ppr_k":20,"typed_hops":2,"max_triples_per_question":30,"aggregate_evidence_token_budget":8000,"same_total_budget_all_arms":True},"answer_contract":{"transport":"LiteLLM","fallback":False,"temperature":0,"answerer":"freeze_before_paid_run"},"arms":["best_fixed_single","category_single","slot_only","divergence_only","sdcr","broadcast"],"frames":frames}
    args.output.mkdir(parents=True,exist_ok=True);(args.output/"query_frames.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")
    lines=["# SDCR Query Suite", "",f"- Frozen frames: {len(frames)}",f"- Track counts: {counts}",f"- Pending complementary candidates excluded: {payload['pending_complementary_candidates']}","","No pending human-adjudication candidate is eligible for paid answer evaluation.",""];(args.output/"RUNBOOK.md").write_text("\n".join(lines));print(args.output/"query_frames.json");return 0
if __name__=="__main__":raise SystemExit(main())
