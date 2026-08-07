#!/usr/bin/env python3
"""Author-adjudicate the qualified held-out set for exploratory evaluation."""
from __future__ import annotations
import argparse,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
REJECT={
 "czr-1464730f-ef834df5":"issuer contamination: the CZR governance question names Consolidated Edison",
 "algn-9b9f2bdd-d7053b5d":"issuer contamination: the ALGN risk question names Allegion plc",
 "pg-60fefd59-79582bd4":"issuer contamination: the PG pair mixes PG&E with Procter & Gamble",
 "mcd-87344e3d-dc315300":"domain contamination: the accounting slot contrasts generic technology and restaurant models",
 "mcd-155b0d36-dc315300":"domain contamination: the accounting slot contrasts generic technology and restaurant models",
 "ip-aabfdb09-d5dd8af4":"issuer contamination: the IP legal slot names Keysight",
 "amat-071eeca7-775094de":"single-view sufficiency risk: both slots ask overlapping governance oversight rather than distinct evidence",
}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=BASE/"heldout_author_adjudication.json");args=parser.parse_args();packet=json.loads((BASE/"heldout_adjudication/annotations.json").read_text());pool=json.loads((BASE/"candidates.json").read_text());by_id={row["candidate_id"]:row for row in pool["candidates"]};rows=[]
    for item in packet["annotations"]:
      cid=item["candidate_id"];candidate=by_id[cid];decision="reject" if cid in REJECT else "accept";rationale=REJECT.get(cid,f"both source slots passed provenance; distinct categories share the declared {', '.join(candidate['shared_decision_axes'])} decision axis")
      rows.append({"candidate_id":cid,"review_mode":"author_adjudicated","both_views_required":"no" if cid=="amat-071eeca7-775094de" else ("uncertain" if cid in REJECT else "yes"),"single_view_sufficient":"yes" if cid=="amat-071eeca7-775094de" else ("uncertain" if cid in REJECT else "no"),"financially_natural":"no" if cid in REJECT else "yes","gold_slots_valid":"uncertain" if cid in REJECT else "yes","decision":decision,"rationale":rationale})
    payload={"contract":"log2026.author_adjudicated_heldout.v1","claim_scope":"exploratory only; no independent reviewers","candidate_count":len(rows),"accepted_count":sum(r["decision"]=="accept" for r in rows),"rejected_count":sum(r["decision"]=="reject" for r in rows),"rows":rows};args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(json.dumps({k:payload[k] for k in ("candidate_count","accepted_count","rejected_count")},indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
