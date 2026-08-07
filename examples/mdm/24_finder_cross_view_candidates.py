#!/usr/bin/env python3
"""Generate output-blind FinDER-derived cross-view candidate questions.

Candidates are all same-issuer, cross-category pairs present in the frozen
category run. Selection uses only query text, case id, and category metadata;
model answers and evaluation scores are never consulted.
"""
from __future__ import annotations
import argparse, importlib.util, json, re
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
EXCLUDED={"ASC","GAAP","SEC","EBITDA","US","FY","CEO","CFO"}

def infer_issuer(query: str) -> str:
    tokens=re.findall(r"\b[A-Z]{2,5}\b",query)
    valid=[token for token in tokens if token not in EXCLUDED]
    return valid[-1] if valid else ""

def build_candidates(rows: list[dict[str,Any]], gold_by_case: dict[str,dict[str,Any]]) -> list[dict[str,Any]]:
    grouped: dict[str,list[dict[str,Any]]]={}
    for row in rows:
        issuer=infer_issuer(str(row.get("query") or ""))
        if issuer:
            grouped.setdefault(issuer,[]).append(row)
    output=[]
    for issuer, items in sorted(grouped.items()):
        for left,right in combinations(sorted(items,key=lambda x:str(x["case_id"])),2):
            if left.get("category")==right.get("category"):
                continue
            lcid,rcid=str(left["case_id"]),str(right["case_id"])
            if lcid not in gold_by_case or rcid not in gold_by_case:
                continue
            output.append({
                "candidate_id":f"{issuer.lower()}-{lcid}-{rcid}","issuer":issuer,
                "construction":"all_same_issuer_cross_category_pairs",
                "selection_uses_model_outputs":False,"human_validation_status":"pending",
                "required_categories":[left["category"],right["category"]],
                "component_case_ids":[lcid,rcid],
                "component_questions":[gold_by_case[lcid]["query"],gold_by_case[rcid]["query"]],
                "required_gold_slots":[gold_by_case[lcid]["expected_answer"],gold_by_case[rcid]["expected_answer"]],
                "paired_question":(
                    f"For {issuer}, answer both evidence requirements using their respective grounded views. "
                    f"(1) {gold_by_case[lcid]['query']} (2) {gold_by_case[rcid]['query']} "
                    "Keep unsupported cross-category causal claims explicit."
                ),
                "claim_scope":"routing-and-slot-completion candidate; not integrative gold until human adjudication",
            })
    return output

def _load_gold() -> dict[str,dict[str,Any]]:
    path=ROOT/"examples/mdm/11_index_providers.py"
    spec=importlib.util.spec_from_file_location("idx_providers",path); assert spec and spec.loader
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return {str(row["case_id"]):row for row in module.load_cases_full(seed=42)}

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--run-prefix",default="fedcat-wide-lite-survivorship-v1")
    ap.add_argument("--output-run-prefix",default="log2026-finder-cross-view-v1"); args=ap.parse_args()
    base=ROOT/"outputs/evaluation/mdm_fedcat"
    aggregate=json.loads((base/args.run_prefix/"category_federation_aggregate.json").read_text())
    candidates=build_candidates(aggregate["records"],_load_gold())
    payload={"contract":"log2026.finder_cross_view_candidates.v1","source_dataset":"FinDER",
             "selection_rule":"all same-issuer cross-category pairs in frozen 80-case run",
             "output_blind":True,"candidate_count":len(candidates),"candidates":candidates}
    out=base/args.output_run_prefix; out.mkdir(parents=True,exist_ok=True)
    (out/"candidates.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")
    lines=["# FinDER-Derived Cross-View Candidates","",f"Candidates: {len(candidates)}","",
           "These are output-blind paired-slot candidates. Human adjudication is required before evaluation.","",
           "| Candidate | Issuer | Categories | Component cases |","|---|---|---|---|"]
    for c in candidates:
        lines.append(f"| `{c['candidate_id']}` | {c['issuer']} | {' ↔ '.join(c['required_categories'])} | {', '.join(c['component_case_ids'])} |")
    (out/"candidates.md").write_text("\n".join(lines)+"\n")
    adjudication=[
      "# FinDER-Derived Cross-View Adjudication Packet","",
      "Reviewers must not inspect model outputs, scores, PPR results, or agent selections while labeling.","",
      "## Label definitions","",
      "- `both_views_required`: neither component view alone supplies both required gold slots.",
      "- `single_view_sufficient`: one component view can supply both slots.",
      "- `financially_natural`: a financial analyst could plausibly ask the paired question as one task.",
      "- `gold_slots_valid`: both original FinDER answers and references support the required slots.",
      "- `decision`: `accept`, `revise`, or `reject`.","",
      "Each candidate requires two independent reviewers. Adjudicate disagreements before freezing the test set.","",
    ]
    annotations=[]
    for c in candidates:
        adjudication.extend([
          f"## {c['candidate_id']}","",f"Issuer: `{c['issuer']}`  ",
          f"Categories: `{'` ↔ `'.join(c['required_categories'])}`  ",
          f"Cases: `{', '.join(c['component_case_ids'])}`","",
          f"Paired question: {c['paired_question']}","",
          f"1. {c['component_questions'][0]}","",f"   Gold slot 1: {c['required_gold_slots'][0]}","",
          f"2. {c['component_questions'][1]}","",f"   Gold slot 2: {c['required_gold_slots'][1]}","",
          "Reviewer checklist:","",
          "- [ ] Both views required: yes / no / uncertain",
          "- [ ] Single view sufficient: yes / no / uncertain",
          "- [ ] Financially natural: yes / no / revise",
          "- [ ] Gold slots valid: yes / no / uncertain",
          "- [ ] Decision: accept / revise / reject",
          "- Rationale:","","---","",
        ])
        annotations.append({"candidate_id":c["candidate_id"],"reviewer_1":None,"reviewer_2":None,
          "adjudicated":None,"allowed_decisions":["accept","revise","reject"]})
    (out/"adjudication_packet.md").write_text("\n".join(adjudication)+"\n")
    (out/"annotations.json").write_text(json.dumps({"contract":"log2026.cross_view_adjudication.v1",
      "blinding":"no model outputs/scores/PPR/agent selections","annotations":annotations},indent=2)+"\n")
    print(f"wrote {out.relative_to(ROOT)}/candidates.json ({len(candidates)} candidates)")
    return 0

if __name__=="__main__": raise SystemExit(main())
