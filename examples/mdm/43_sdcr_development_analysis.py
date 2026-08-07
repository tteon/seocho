#!/usr/bin/env python3
"""Paired development analysis with schema failures scored as execution failures."""
from __future__ import annotations
import argparse,json,random
from pathlib import Path
from statistics import mean

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"

def interval(values:list[float],seed:int=42,reps:int=10000)->list[float]:
    rng=random.Random(seed);samples=sorted(mean(rng.choice(values) for _ in values) for _ in range(reps));return [samples[int(.025*reps)],samples[int(.975*reps)]]

def analyze(payload:dict)->dict:
    grouped={}
    for row in payload["rows"]:grouped.setdefault(row["candidate_id"],{})[row["arm"]]=row
    def score(row):return 0.0 if row.get("response",{}).get("parse_error") else row["token_f1"]
    arms=("left_single","right_single","sdcr_coalition");itt={arm:mean(score(rows[arm]) for rows in grouped.values()) for arm in arms};fixed=max(("left_single","right_single"),key=lambda arm:itt[arm]);deltas=[score(rows["sdcr_coalition"])-score(rows[fixed]) for rows in grouped.values()];complete=[rows for rows in grouped.values() if not any(rows[arm].get("response",{}).get("parse_error") for arm in arms)];complete_delta=[rows["sdcr_coalition"]["token_f1"]-rows[fixed]["token_f1"] for rows in complete]
    return {"contract":"log2026.sdcr_paired_analysis.v1","track":payload.get("track","unspecified"),"cases":len(grouped),"schema_failures":sum(row.get("response",{}).get("parse_error",False) for row in payload["rows"]),"failure_policy":"schema failure scores zero in intention-to-treat","intention_to_treat":{"mean_token_f1":itt,"best_fixed_single":fixed,"coalition_delta":mean(deltas),"bootstrap_95_ci":interval(deltas),"win_tie_loss":[sum(d>0 for d in deltas),sum(d==0 for d in deltas),sum(d<0 for d in deltas)]},"complete_case":{"cases":len(complete),"coalition_delta":mean(complete_delta) if complete_delta else None,"bootstrap_95_ci":interval(complete_delta) if complete_delta else None,"win_tie_loss":[sum(d>0 for d in complete_delta),sum(d==0 for d in complete_delta),sum(d<0 for d in complete_delta)]}}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,default=BASE/"development_answers.json");parser.add_argument("--output",type=Path,default=BASE/"development_analysis.json");args=parser.parse_args();result=analyze(json.loads(args.input.read_text()));args.output.write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
