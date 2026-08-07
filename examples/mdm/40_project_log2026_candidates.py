#!/usr/bin/env python3
"""Non-destructively project qualified full-FinDER cases into LoG-only DBs."""
from __future__ import annotations
import argparse,json,os,sys,yaml
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));BASE=ROOT/"outputs/evaluation/mdm_fedcat";SCENARIO="duplicate-aware-survivorship-v1-fibo-finance-core";BATCH=500

def workspace(provider:str,case_id:str)->str:return f"fedcat-scenario-{SCENARIO}-{provider}-{case_id}"

def main()->int:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry
    parser=argparse.ArgumentParser();parser.add_argument("--config",type=Path,default=ROOT/"examples/mdm/config/category_databases_log2026.yaml");parser.add_argument("--output",type=Path,default=BASE/"log2026-full-finder-cross-view-v1/projection.json");args=parser.parse_args();load_dotenv(ROOT/".env")
    auth=(os.environ.get("NEO4J_USER","neo4j"),os.environ["NEO4J_PASSWORD"]);uri=os.environ.get("NEO4J_URI","bolt://localhost:7687");providers=yaml.safe_load((ROOT/"examples/mdm/config/provider_databases.yaml").read_text())["instances"];targets={spec["category"]:spec for spec in yaml.safe_load(args.config.read_text())["categories"].values()};pool=json.loads((BASE/"log2026-full-finder-cross-view-v1/candidates.json").read_text());by_candidate={row["candidate_id"]:row for row in pool["candidates"]};gate=json.loads((BASE/"log2026-full-finder-cross-view-v1/provenance_gate.json").read_text());cases:dict[str,set[str]]=defaultdict(set)
    for row in gate["rows"]:
      if row["automatic_provenance_pass"]:
        candidate=by_candidate[row["candidate_id"]]
        for cid,category in zip(candidate["component_case_ids"],candidate["required_categories"]):cases[category].add(cid)
    source=GraphDatabase.driver(uri,auth=auth);results=[]
    try:
      for category,case_ids in sorted(cases.items()):
        target=targets[category];database=target["database"];store=Neo4jGraphStore(uri,*auth);db_registry.register(database);store.ensure_database(database,wait_online=True);store.close();dest=GraphDatabase.driver(uri,auth=auth)
        try:
          with dest.session(database=database) as session:
            foreign=session.run("MATCH (n) WHERE n.projection_run IS NULL OR n.projection_run <> 'log2026-qualified-v1' RETURN count(n) AS n").single()["n"]
            if foreign:raise RuntimeError(f"refusing target with foreign nodes {database}: {foreign}")
            completed={(row["provider_id"],row["case_id"]) for row in session.run("MATCH (n {projection_run:'log2026-qualified-v1'}) RETURN DISTINCT n.provider_id AS provider_id,n.case_id AS case_id")}
          for provider_id,spec in providers.items():
            source_db=spec["database"]
            pending=[cid for cid in sorted(case_ids) if (provider_id,cid) not in completed]
            if not pending:continue
            ws_to_case={workspace(provider_id,cid):cid for cid in pending};workspaces=list(ws_to_case)
            with source.session(database=source_db) as session:
              nodes=session.run("MATCH (n) WHERE n._workspace_id IN $workspaces RETURN n._workspace_id AS ws,elementId(n) AS eid,labels(n) AS labels,properties(n) AS props",workspaces=workspaces).data();rels=session.run("MATCH (a)-[r]->(b) WHERE a._workspace_id IN $workspaces AND b._workspace_id=a._workspace_id RETURN a._workspace_id AS ws,elementId(a) AS src,elementId(b) AS dst,type(r) AS type,properties(r) AS props",workspaces=workspaces).data()
            enriched=[]
            for node in nodes:
              cid=ws_to_case[node["ws"]];props={**(node["props"] or {}),"case_id":cid,"category":category,"provider_id":provider_id,"provider_database":source_db,"source_provider_eid":node["eid"],"projection_run":"log2026-qualified-v1"};props.pop("embedding",None);props.pop("embedding_vector",None);enriched.append({"labels":node["labels"],"props":props})
            with dest.session(database=database) as session:
              for start in range(0,len(enriched),BATCH):session.run("UNWIND $rows AS row CALL apoc.create.node(row.labels,row.props) YIELD node RETURN count(node)",rows=enriched[start:start+BATCH]).consume()
              for start in range(0,len(rels),BATCH):
                batch=[]
                for rel in rels[start:start+BATCH]:
                  cid=ws_to_case[rel["ws"]];batch.append({"src":rel["src"],"dst":rel["dst"],"type":rel["type"],"props":{**(rel["props"] or {}),"case_id":cid,"category":category,"provider_id":provider_id,"provider_database":source_db,"projection_run":"log2026-qualified-v1"}})
                session.run("UNWIND $rows AS row MATCH (a {source_provider_eid:row.src,provider_id:row.props.provider_id}),(b {source_provider_eid:row.dst,provider_id:row.props.provider_id}) CALL apoc.create.relationship(a,row.type,row.props,b) YIELD rel RETURN count(rel)",rows=batch).consume()
          with dest.session(database=database) as session:
            totals=session.run("MATCH (n {projection_run:'log2026-qualified-v1'}) WITH count(n) AS nodes,count(DISTINCT n.provider_id+'|'+n.case_id) AS workspaces OPTIONAL MATCH ()-[r {projection_run:'log2026-qualified-v1'}]->() RETURN nodes,workspaces,count(r) AS relationships").single()
          results.append({"category":category,"database":database,"case_count":len(case_ids),"provider_workspaces":totals["workspaces"],"nodes":totals["nodes"],"relationships":totals["relationships"]})
        finally:dest.close()
    finally:source.close()
    payload={"contract":"log2026.safe_category_projection.v1","source_scenario":SCENARIO,"target_namespace":"log26cat*","destructive_operations":0,"results":results,"total_nodes":sum(r["nodes"] for r in results),"total_relationships":sum(r["relationships"] for r in results)};args.output.write_text(json.dumps(payload,indent=2)+"\n");print(json.dumps(payload,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
