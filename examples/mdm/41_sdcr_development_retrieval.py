#!/usr/bin/env python3
"""Equal-budget PPR retrieval replay for qualified development coalitions."""
from __future__ import annotations
import argparse,json,math,re
from pathlib import Path
from statistics import mean
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1";STOP={"and","for","from","that","the","this","with","impact","what"}

def tokens(value:Any)->set[str]:return {t for t in re.findall(r"[a-z0-9]+",str(value).lower()) if len(t)>=3 and t not in STOP}
def numbers(value:Any)->set[str]:return {x.replace(",","").replace("$","").rstrip("%") for x in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?",str(value))}
def node_text(node:dict[str,Any])->str:return " ".join([*node["labels"],*(str(v) for v in node["props"].values())])

def personalized_rank(view:dict[str,Any],question:str)->tuple[list[dict[str,Any]],str]:
    nodes=view["nodes"];index={n["id"]:i for i,n in enumerate(nodes)};q=tokens(question);lex=[len(q&tokens(node_text(n))) for n in nodes];seed_ids=sorted(range(len(nodes)),key=lambda i:(-lex[i],i))[:min(3,len(nodes))]
    if not view["triples"]:return [nodes[i] for i in sorted(range(len(nodes)),key=lambda i:(-lex[i],i))],"lexical_fallback"
    adj=[set() for _ in nodes]
    for edge in view["triples"]:
      if edge["source"] in index and edge["target"] in index:
        a,b=index[edge["source"]],index[edge["target"]];adj[a].add(b);adj[b].add(a)
    n=len(nodes);personal=[0.0]*n
    for i in seed_ids:personal[i]=1/len(seed_ids)
    rank=personal[:];d=.85
    for _ in range(100):
      nxt=[(1-d)*p for p in personal];dangling=sum(rank[i] for i in range(n) if not adj[i])
      for j in range(n):nxt[j]+=d*dangling*personal[j]
      for i,neighbors in enumerate(adj):
        for j in neighbors:nxt[j]+=d*rank[i]/len(neighbors)
      if sum(abs(a-b) for a,b in zip(rank,nxt))<1e-10:rank=nxt;break
      rank=nxt
    order=sorted(range(n),key=lambda i:(-rank[i],-lex[i],i));return [nodes[i] for i in order],"ppr"

def coverage(nodes:list[dict[str,Any]],gold:str)->dict[str,float|None]:
    text=" ".join(node_text(n) for n in nodes);gt=tokens(gold);gn=numbers(gold);return {"token_recall":len(gt&tokens(text))/len(gt) if gt else None,"number_recall":len(gn&numbers(text))/len(gn) if gn else None}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=BASE/"development_retrieval.json");parser.add_argument("--split",choices=("development","held_out"),default="development");parser.add_argument("--author-adjudication",type=Path);args=parser.parse_args();audit=json.loads((BASE/"evidence_audit_log26.json").read_text());pool=json.loads((BASE/"candidates.json").read_text());by_id={row["candidate_id"]:row for row in pool["candidates"]};allowed=None
    if args.author_adjudication:allowed={row["candidate_id"] for row in json.loads(args.author_adjudication.read_text())["rows"] if row["decision"]=="accept"}
    rows=[]
    for item in audit["rows"]:
      if item["split"]!=args.split or (allowed is not None and item["candidate_id"] not in allowed):continue
      candidate=by_id[item["candidate_id"]];ranked=[];methods=[]
      for view,question in zip(item["views"],candidate["component_questions"]):
        order,method=personalized_rank(view,question);ranked.append(order);methods.append(method)
      arms={"left_single":ranked[0][:20],"right_single":ranked[1][:20],"sdcr_coalition":ranked[0][:10]+ranked[1][:10]};arm_results={}
      for name,evidence in arms.items():arm_results[name]={"node_budget":len(evidence),"slot_1":coverage(evidence,candidate["required_gold_slots"][0]),"slot_2":coverage(evidence,candidate["required_gold_slots"][1])}
      rows.append({"candidate_id":item["candidate_id"],"retrieval_methods":methods,"arms":arm_results,"evidence":{"left_single":arms["left_single"],"right_single":arms["right_single"],"sdcr_coalition":arms["sdcr_coalition"]}})
    summary={name:{"mean_slot_token_recall":mean((row["arms"][name]["slot_1"]["token_recall"]+row["arms"][name]["slot_2"]["token_recall"])/2 for row in rows),"mean_slot_number_recall":mean((row["arms"][name]["slot_1"]["number_recall"]+row["arms"][name]["slot_2"]["number_recall"])/2 for row in rows if row["arms"][name]["slot_1"]["number_recall"] is not None and row["arms"][name]["slot_2"]["number_recall"] is not None)} for name in ("left_single","right_single","sdcr_coalition")}
    payload={"contract":"log2026.sdcr_equal_budget_retrieval.v1","split":args.split,"claim_scope":"exploratory author-adjudicated" if args.author_adjudication else "development","cases":len(rows),"fixed_total_node_budget":20,"ppr_damping":.85,"ppr_top_k_per_coalition_view":10,"summary":summary,"rows":rows};args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(json.dumps(summary,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
