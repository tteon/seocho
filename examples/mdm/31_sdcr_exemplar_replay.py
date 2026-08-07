#!/usr/bin/env python3
"""Five transparent SDCR exemplar queries and deterministic $0 replay."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"outputs/evaluation/mdm_fedcat/log2026-sdcr-exemplars-v1"

CASES=[
 {"query_id":"natural-01d03a27","track":"natural_local","question":"Xylem’s (XYL) product rev delta from 2022 to 2023.","required_slots":["product_revenue_2022","product_revenue_2023","currency_unit","revenue_delta","calculation","provenance"],"permitted_graph_ids":["financials","accounting","footnotes"],"specialists":{"financials":["product_revenue_2022","product_revenue_2023","currency_unit","revenue_delta","calculation","provenance"],"accounting":["currency_unit"],"footnotes":[]},"conflict":False,"expected_mode":"single","why":"local control: penalizes unnecessary fan-out"},
 {"query_id":"complementary-aapl-71b18930-8f7bac2f","track":"complementary","question":"For AAPL, report Q3 2024 repurchase volume and spending, and separately explain material legal risks to growth and financial health.","required_slots":["repurchased_share_count","repurchase_total_spend","repurchase_period","material_legal_matters","maximum_regulatory_exposure","growth_or_financial_risk","provenance_per_slot"],"permitted_graph_ids":["shareholder_return","legal","risk"],"specialists":{"shareholder_return":["repurchased_share_count","repurchase_total_spend","repurchase_period","provenance_per_slot"],"legal":["material_legal_matters","maximum_regulatory_exposure","growth_or_financial_risk","provenance_per_slot"],"risk":["growth_or_financial_risk"]},"conflict":False,"expected_mode":"complementary_coalition","why":"positive slot-distribution case; pending human adjudication before paid use"},
 {"query_id":"poison-01d03a27-silo-minimax25","track":"verification_conflict","question":"Xylem’s (XYL) product rev delta from 2022 to 2023.","required_slots":["product_revenue_2022","product_revenue_2023","currency_unit","revenue_delta","provenance"],"permitted_graph_ids":["financials_primary","financials_verifier"],"specialists":{"financials_primary":["product_revenue_2022","product_revenue_2023","currency_unit","revenue_delta","provenance"],"financials_verifier":["product_revenue_2022","product_revenue_2023","currency_unit","revenue_delta","provenance"]},"comparable_key":{"metric":"product_revenue","period_start":"2022","period_end":"2023","unit":"USD","basis":"reported","segment":"product"},"conflict":True,"expected_mode":"verification_coalition","why":"tests poison detection without relying on generic graph divergence"},
 {"query_id":"protected-01d03a27-silo-minimax25","track":"governance","question":"Xylem’s (XYL) product rev delta from 2022 to 2023.","required_slots":["product_revenue_2022","product_revenue_2023","revenue_delta","provenance"],"permitted_graph_ids":["financials"],"denied_fields":["_synthetic_protected_value"],"specialists":{"financials":["product_revenue_2022","product_revenue_2023","revenue_delta","provenance"]},"conflict":False,"expected_mode":"single","why":"policy filtering precedes routing; protected marker must never enter evidence"},
 {"query_id":"unanswerable-xyl-europe-constant-currency","track":"unanswerable","question":"What was XYL’s product revenue increase in 2023 on a Europe-only constant-currency basis?","required_slots":["product_revenue_2022","product_revenue_2023","europe_segment","constant_currency_basis","derived_delta","provenance"],"permitted_graph_ids":["financials","footnotes"],"specialists":{"financials":["product_revenue_2022","product_revenue_2023","provenance"],"footnotes":[]},"conflict":False,"expected_mode":"abstain","why":"tests conservative missing-slot behavior"},
]

def replay(case:dict[str,Any])->dict[str,Any]:
    required=set(case["required_slots"]); candidates={gid:set(slots)&required for gid,slots in case["specialists"].items() if gid in case["permitted_graph_ids"]}; selected=[];covered=set()
    while covered!=required:
        ranked=sorted(((len(slots-covered),gid) for gid,slots in candidates.items() if gid not in selected),key=lambda item:(-item[0],item[1]))
        if not ranked or ranked[0][0]<=0:break
        gid=ranked[0][1];selected.append(gid);covered|=candidates[gid]
        if covered==required:break
    if case.get("conflict") and covered==required:
        for gid in sorted(candidates):
            if gid not in selected:selected.append(gid);break
    missing=sorted(required-covered)
    mode="abstain" if missing else "verification_coalition" if case.get("conflict") and len(selected)>1 else "single" if len(selected)==1 else "complementary_coalition"
    return {"query_id":case["query_id"],"mode":mode,"selected_graph_ids":selected,"covered_slots":sorted(covered),"missing_slots":missing,"matches_expected":mode==case["expected_mode"],"trigger":"comparable_fact_conflict" if case.get("conflict") else "required_slot_gap" if len(selected)>1 or missing else "none"}

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=OUT);args=ap.parse_args();results=[replay(case) for case in CASES];payload={"contract":"log2026.sdcr_exemplar_replay.v1","paid_calls":0,"cases":CASES,"results":results,"all_expected":all(row["matches_expected"] for row in results)};args.output.mkdir(parents=True,exist_ok=True);(args.output/"replay.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")
    lines=["# SDCR Exemplar Replay","",f"All expected: {payload['all_expected']}","","| Query | Track | Expected | Actual | Selected | Missing | Trigger |","|---|---|---|---|---|---|---|"]
    for case,row in zip(CASES,results):lines.append(f"| `{case['query_id']}` | {case['track']} | {case['expected_mode']} | {row['mode']} | {', '.join(row['selected_graph_ids'])} | {', '.join(row['missing_slots']) or 'none'} | {row['trigger']} |")
    (args.output/"replay.md").write_text("\n".join(lines)+"\n");print(args.output/"replay.json");return 0 if payload["all_expected"] else 1
if __name__=="__main__":raise SystemExit(main())
