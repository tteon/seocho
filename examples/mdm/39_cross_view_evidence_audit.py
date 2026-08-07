#!/usr/bin/env python3
"""Freeze read-only graph evidence for provenance-qualified cross-view cases."""
from __future__ import annotations
import argparse,importlib.util,json,os,yaml
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/"outputs/evaluation/mdm_fedcat";KEEP={"id","name","value","period","basis","category","case_id","provider_id","provider_database","source_id","source_type","workspace_id","prompt_id","ontology_hash","ontology_modules"}

def load_freezer():
    path=ROOT/"examples/mdm/35_sdcr_freeze_evidence.py";spec=importlib.util.spec_from_file_location("freezer35",path);assert spec and spec.loader
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def slim(props:dict[str,Any])->dict[str,Any]:return {key:value for key,value in props.items() if key in KEEP}

def main()->int:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    parser=argparse.ArgumentParser();parser.add_argument("--max-nodes",type=int,default=40);parser.add_argument("--max-triples",type=int,default=20);parser.add_argument("--config",type=Path,default=ROOT/"examples/mdm/config/category_databases.yaml");parser.add_argument("--output",type=Path,default=BASE/"log2026-full-finder-cross-view-v1/evidence_audit.json");args=parser.parse_args();load_dotenv(ROOT/".env");freezer=load_freezer()
    pool=json.loads((BASE/"log2026-full-finder-cross-view-v1/candidates.json").read_text());candidate_by_id={row["candidate_id"]:row for row in pool["candidates"]};gate=json.loads((BASE/"log2026-full-finder-cross-view-v1/provenance_gate.json").read_text());qualified=[row for row in gate["rows"] if row["automatic_provenance_pass"]];cfg=yaml.safe_load(args.config.read_text())["categories"];db_by_category={str(spec["category"]):str(spec["database"]) for spec in cfg.values()};driver=GraphDatabase.driver(os.environ.get("NEO4J_URI","bolt://localhost:7687"),auth=(os.environ.get("NEO4J_USER","neo4j"),os.environ["NEO4J_PASSWORD"]));rows=[]
    try:
      for audit in qualified:
        candidate=candidate_by_id[audit["candidate_id"]];views=[]
        for cid,category,question in zip(candidate["component_case_ids"],candidate["required_categories"],candidate["component_questions"]):
          database=db_by_category[category]
          with driver.session(database=database) as session:
            nodes=[{"id":str(row["id"]),"labels":list(row["labels"] or []),"props":slim(freezer.clean(dict(row["props"] or {})))} for row in session.run("MATCH (n) WHERE n.case_id=$c RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props",c=cid)]
            nodes=sorted(nodes,key=lambda row:freezer.rank_node(row,question))[:args.max_nodes];ids={row["id"] for row in nodes};triples=[]
            for edge in session.run("MATCH (a)-[r]->(b) WHERE a.case_id=$c AND b.case_id=$c RETURN elementId(a) AS a,type(r) AS type,elementId(b) AS b",c=cid):
              if str(edge["a"]) in ids and str(edge["b"]) in ids:triples.append({"source":str(edge["a"]),"type":str(edge["type"]),"target":str(edge["b"])})
            views.append({"case_id":cid,"category":category,"database":database,"node_count":len(nodes),"triple_count":min(len(triples),args.max_triples),"missing":not nodes,"nodes":nodes,"triples":triples[:args.max_triples]})
        rows.append({"candidate_id":candidate["candidate_id"],"split":candidate["split"],"both_graph_views_present":all(not view["missing"] for view in views),"views":views})
    finally:driver.close()
    payload={"contract":"log2026.cross_view_evidence_audit.v1","read_only":True,"qualified_candidates":len(rows),"both_views_present":sum(row["both_graph_views_present"] for row in rows),"development":sum(row["split"]=="development" for row in rows),"held_out":sum(row["split"]=="held_out" for row in rows),"rows":rows};args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n");print(json.dumps({key:payload[key] for key in ("qualified_candidates","both_views_present","development","held_out")},indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
