#!/usr/bin/env python3
"""Create a model-output-blind two-reviewer packet for 35 held-out candidates."""
from __future__ import annotations
import argparse,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=BASE/"heldout_adjudication");args=parser.parse_args();pool=json.loads((BASE/"candidates.json").read_text());by_id={row["candidate_id"]:row for row in pool["candidates"]};gate=json.loads((BASE/"provenance_gate.json").read_text());ids=[row["candidate_id"] for row in gate["rows"] if row["split"]=="held_out" and row["automatic_provenance_pass"]];args.output.mkdir(parents=True,exist_ok=True)
    lines=["# Held-out Cross-View Adjudication Packet","","Do not inspect graph retrieval, model answers, or evaluation scores while reviewing.","","Accept only if both original gold slots are reference-supported, neither component view alone supplies both slots, and the combined question is financially natural.",""]
    annotations=[]
    for number,cid in enumerate(ids,1):
      row=by_id[cid];lines.extend([f"## H{number:02d}","",f"Candidate key: `{cid}`  ",f"Issuer: `{row['issuer']}`  ",f"Categories: `{' ↔ '.join(row['required_categories'])}`  ",f"Decision axes: `{', '.join(row['shared_decision_axes'])}`","",f"1. {row['component_questions'][0]}","",f"   Gold slot 1: {row['required_gold_slots'][0]}","",f"2. {row['component_questions'][1]}","",f"   Gold slot 2: {row['required_gold_slots'][1]}","","Reviewer fields:","","- Both views required: yes / no / uncertain","- Single view sufficient: yes / no / uncertain","- Financially natural: yes / no / revise","- Gold slots valid: yes / no / uncertain","- Decision: accept / revise / reject","- Rationale:","","---",""])
      annotations.append({"candidate_id":cid,"reviewer_1":None,"reviewer_2":None,"adjudicated":None})
    (args.output/"packet.md").write_text("\n".join(lines)+"\n");(args.output/"annotations.json").write_text(json.dumps({"contract":"log2026.heldout_adjudication.v1","blinding":"no graph retrieval/model answers/evaluation scores","candidate_count":len(ids),"annotations":annotations},indent=2)+"\n");print(f"heldout candidates={len(ids)}");return 0
if __name__=="__main__":raise SystemExit(main())
