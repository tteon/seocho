#!/usr/bin/env python3
"""Freeze read-only, case-scoped evidence bundles for held-out SDCR queries."""
from __future__ import annotations
import argparse,json,os,re,yaml
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat";INFRA={"Document","DocumentVersion","Section","Chunk","Memory","SourceRef"}

def clean(value:Any)->Any:
    if value is None or isinstance(value,(str,int,float,bool)):return value
    if isinstance(value,(list,tuple)):return [clean(item) for item in value]
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items() if not str(k).startswith("embedding")}
    return str(value)

STOP={"a","an","and","as","at","by","for","from","in","is","of","on","or","the","to","was","what","with"}
SYNONYMS={"rev":{"revenue"},"delta":{"change","difference"},"buyback":{"repurchase"},"sales":{"revenue"}}

def tokens(value:Any)->set[str]:
    base={token for token in re.findall(r"[a-z0-9]+",str(value).lower()) if len(token)>1 and token not in STOP}
    return base|{expanded for token in base for expanded in SYNONYMS.get(token,set())}

def rank_node(row:dict[str,Any],question:str="")->tuple[Any,...]:
    props=row["props"];labels=set(row["labels"]);query=tokens(question);text=tokens(" ".join([*map(str,row["labels"]),str(props.get("name","")),str(props.get("id","")),str(props.get("period","")),str(props.get("basis",""))]));overlap=len(query&text)
    return (-overlap,0 if props.get("value") is not None else 1,0 if labels-INFRA else 1,0 if props.get("name") else 1,str(props.get("name","")),row["id"])

def main()->int:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=BASE/"log2026-sdcr-evidence-v1");ap.add_argument("--max-nodes",type=int,default=60);ap.add_argument("--max-triples",type=int,default=30);args=ap.parse_args();load_dotenv(ROOT/".env")
    suite=json.loads((BASE/"log2026-sdcr-query-suite-v1/query_frames.json").read_text());natural=[row for row in suite["frames"] if row["track"]=="natural_local"];cfg=yaml.safe_load((ROOT/"examples/mdm/config/category_databases.yaml").read_text())["categories"];db_by_category={str(spec["category"]):str(spec["database"]) for spec in cfg.values()};aggregate=json.loads((BASE/"fedcat-wide-lite-survivorship-v1/category_federation_aggregate.json").read_text())["records"];category_by_case={row["case_id"]:row["category"] for row in aggregate};driver=GraphDatabase.driver(os.environ.get("NEO4J_URI","bolt://localhost:7687"),auth=(os.environ.get("NEO4J_USER","neo4j"),os.environ["NEO4J_PASSWORD"]));bundles=[]
    try:
      for frame in natural:
        cid=frame["component_case_ids"][0];category=category_by_case[cid];database=db_by_category[category]
        with driver.session(database=database) as session:
          nodes=[{"id":str(row["id"]),"labels":list(row["labels"] or []),"props":clean(dict(row["props"] or {}))} for row in session.run("MATCH (n) WHERE n.case_id=$c RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props",c=cid)]
          nodes=sorted(nodes,key=lambda row:rank_node(row,frame["question"]))[:args.max_nodes];ids={row["id"] for row in nodes}
          triples=[]
          for row in session.run("MATCH (a)-[r]->(b) WHERE a.case_id=$c AND b.case_id=$c RETURN elementId(a) AS a, type(r) AS type, elementId(b) AS b, properties(r) AS props",c=cid):
            if str(row["a"]) in ids and str(row["b"]) in ids:triples.append({"source":str(row["a"]),"type":str(row["type"]),"target":str(row["b"]),"props":clean(dict(row["props"] or {}))})
          triples=triples[:args.max_triples];providers=sorted({str(node["props"].get("provider_id")) for node in nodes if node["props"].get("provider_id")});bundles.append({"query_id":frame["query_id"],"case_id":cid,"question":frame["question"],"category":category,"graph_id":category,"database":database,"nodes":nodes,"triples":triples,"provider_ids":providers,"node_count":len(nodes),"triple_count":len(triples),"missing_evidence":not nodes,"read_only_source":True})
    finally:driver.close()
    payload={"contract":"log2026.sdcr_evidence.v1","snapshot":"duplicate_aware_survivorship@v1__fibo_finance_core","query_count":len(bundles),"complete":all(not row["missing_evidence"] for row in bundles),"max_nodes":args.max_nodes,"max_triples":args.max_triples,"bundles":bundles};args.output.mkdir(parents=True,exist_ok=True);(args.output/"evidence_bundles.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");lines=["# Frozen SDCR Evidence Bundles","",f"- Queries: {len(bundles)}",f"- Non-empty: {sum(not row['missing_evidence'] for row in bundles)}",f"- Mean nodes: {sum(row['node_count'] for row in bundles)/len(bundles):.1f}",f"- Mean triples: {sum(row['triple_count'] for row in bundles)/len(bundles):.1f}","", "All database reads were case-scoped and read-only.",""];(args.output/"evidence_bundles.md").write_text("\n".join(lines));print(args.output/"evidence_bundles.json");return 0
if __name__=="__main__":raise SystemExit(main())
