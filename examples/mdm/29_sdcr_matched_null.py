#!/usr/bin/env python3
"""Build the matched cross-model PPR null for SDCR, read-only.

Provider-specific category graphs hold case/category/profile fixed while the
generation model changes. Their entity-seeded PPR divergence estimates ordinary
generator-induced graph variability without using answer labels.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FEDCAT = ROOT / "outputs/evaluation/mdm_fedcat"
PARTIALS = FEDCAT / "fedcat-full-all-survivorship-v1/index_partial"
CROSS = FEDCAT / "log2026-full-multiagent-network-v1/analysis.json"
OUT = FEDCAT / "log2026-sdcr-null-v1"
PREFIX = "fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-"
INFRA = {"Document", "DocumentVersion", "Section", "Chunk", "Memory", "SourceRef"}
STOP = {"the", "company", "entity", "policy", "risk", "revenue", "cost", "costs", "financial", "information", "data", "result", "results", "other", "none", "reporting company", "the company"}


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower())).strip()


def valid(name: str) -> bool:
    return len(name) >= 3 and name not in STOP and not name.endswith(" unspecified")


def workspace_categories() -> dict[str, str]:
    result = {}
    for path in PARTIALS.glob("*.json"):
        row = json.loads(path.read_text())
        if not row.get("error"): result[str(row["workspace_id"])] = str(row["category"])
    return result


def export_provider_graphs(entity_names: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    import yaml
    load_dotenv(ROOT / ".env")
    mapping = workspace_categories()
    config = yaml.safe_load((ROOT / "examples/mdm/config/provider_databases.yaml").read_text())
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
    graphs: dict[str, dict[str, Any]] = {}
    audit = {"raw_nodes": 0, "raw_edges": 0, "read_only": True}
    for provider, spec in config["instances"].items():
        driver = GraphDatabase.driver(spec["uri"], auth=auth)
        raw_to_key: dict[str, tuple[str, str]] = {}; nodes: dict[tuple[str, str], int] = {}; observations: Counter[tuple[str, str]] = Counter()
        try:
            with driver.session(database=spec["database"]) as session:
                for row in session.run("MATCH (n) WHERE n._workspace_id STARTS WITH $p RETURN elementId(n) AS id, n._workspace_id AS w, coalesce(n.name,'') AS name, labels(n) AS labels", p=PREFIX):
                    audit["raw_nodes"] += 1; category = mapping.get(str(row["w"] or "")); name = normalize(str(row["name"] or "")); labels = set(row["labels"] or [])
                    if not category or not valid(name) or labels <= INFRA: continue
                    key = (category, name); nodes.setdefault(key, len(nodes)); observations[key] += 1; raw_to_key[str(row["id"])] = key
                edge_counts: Counter[tuple[int, int]] = Counter()
                for row in session.run("MATCH (a)-[r]->(b) WHERE a._workspace_id STARTS WITH $p RETURN elementId(a) AS a, elementId(b) AS b", p=PREFIX):
                    audit["raw_edges"] += 1; left, right = raw_to_key.get(str(row["a"])), raw_to_key.get(str(row["b"]))
                    if left and right and left[0] == right[0] and left != right: edge_counts[(nodes[left], nodes[right])] += 1
        finally: driver.close()
        graphs[provider] = {"nodes": [{"id": index, "category": key[0], "name": key[1], "observations": observations[key]} for key, index in nodes.items()], "edges": [[a,b,w] for (a,b),w in edge_counts.items()]}
    audit["providers"] = sorted(graphs); audit["entity_selection_count"] = len(entity_names)
    return graphs, audit


def ppr_neighbors(graphs: dict[str, Any], names: set[str], top_k: int = 20) -> dict[tuple[str, str, str], list[str]]:
    import networkx as nx
    output = {}
    for provider, data in graphs.items():
        by_id = {node["id"]: node for node in data["nodes"]}; lookup = {(node["category"], node["name"]): node["id"] for node in data["nodes"]}
        categories = sorted({node["category"] for node in data["nodes"]})
        for category in categories:
            graph = nx.DiGraph(); graph.add_nodes_from(node["id"] for node in data["nodes"] if node["category"] == category)
            for left,right,weight in data["edges"]:
                if by_id[left]["category"] == category:
                    graph.add_edge(left,right,weight=weight); graph.add_edge(right,left,weight=weight)
            for name in names:
                root = lookup.get((category,name))
                if root is None: continue
                scores = nx.pagerank(graph, alpha=.85, personalization={root:1.0}, weight="weight", max_iter=200, tol=1e-8)
                ranked = [by_id[node]["name"] for node,_ in sorted(scores.items(), key=lambda item:(-item[1],item[0])) if node != root][:top_k]
                output[(provider,category,name)] = ranked
    return output


def jaccard_divergence(left: list[str], right: list[str]) -> float:
    lset,rset=set(left),set(right); union=lset|rset
    return 1-len(lset&rset)/len(union) if union else 0.0


def rank_weighted_divergence(left: list[str], right: list[str], depth: int = 10) -> float:
    """Reciprocal-rank weighted Jaccard divergence at a fixed depth."""
    left_weights = {name: 1 / (index + 1) for index, name in enumerate(left[:depth])}
    right_weights = {name: 1 / (index + 1) for index, name in enumerate(right[:depth])}
    names = set(left_weights) | set(right_weights)
    denominator = sum(max(left_weights.get(name, 0), right_weights.get(name, 0)) for name in names)
    if not denominator:
        return 0.0
    overlap = sum(min(left_weights.get(name, 0), right_weights.get(name, 0)) for name in names)
    return 1 - overlap / denominator


def null_rows(retrieved: dict[tuple[str,str,str],list[str]]) -> list[dict[str,Any]]:
    by_context: dict[tuple[str,str],list[str]]=defaultdict(list)
    for provider,category,name in retrieved: by_context[(category,name)].append(provider)
    rows=[]
    for (category,name),providers in sorted(by_context.items()):
        providers=sorted(set(providers))
        for index,left in enumerate(providers):
            for right in providers[index+1:]:
                lrows, rrows = retrieved[(left,category,name)], retrieved[(right,category,name)]
                rows.append({"category":category,"entity":name,"left_provider":left,"right_provider":right,"ppr20_divergence":round(jaccard_divergence(lrows,rrows),6),"rank_weighted_divergence":round(rank_weighted_divergence(lrows,rrows),6)})
    return rows


def tail_pvalue(value: float, null: list[float]) -> float:
    return (1+sum(candidate>=value for candidate in null))/(len(null)+1)


def auc(null: list[float], observed: list[float]) -> float:
    # Probability that a random cross-view score exceeds a random null score, ties=.5.
    wins=ties=0
    for positive in observed:
        wins += sum(positive>negative for negative in null); ties += sum(positive==negative for negative in null)
    return (wins+.5*ties)/(len(observed)*len(null)) if observed and null else 0.0


def leave_one_out_false_trigger(null: list[float], alpha: float) -> float:
    hits=0
    for index,value in enumerate(null):
        reference=null[:index]+null[index+1:]
        hits += tail_pvalue(value,reference)<=alpha
    return hits/len(null) if null else 0.0


def summarize(null_rows_data: list[dict[str,Any]], cross_rows: list[dict[str,Any]]) -> dict[str,Any]:
    null=[float(row["rank_weighted_divergence"]) for row in null_rows_data]
    observed=[rank_weighted_divergence(row.get("left_top",[]),row.get("right_top",[])) for row in cross_rows]
    sensitivity=[]
    for alpha in (.01,.025,.05,.1,.2):
        sensitivity.append({"alpha":alpha,"null_false_trigger":round(leave_one_out_false_trigger(null,alpha),6),"cross_trigger_rate":round(sum(tail_pvalue(value,null)<=alpha for value in observed)/len(observed),6)})
    return {"metric":"reciprocal-rank weighted PPR neighborhood divergence at depth 10","null_pairs":len(null),"cross_view_pairs":len(observed),"null_mean":round(mean(null),6),"null_median":round(median(null),6),"cross_mean":round(mean(observed),6),"cross_median":round(median(observed),6),"auroc":round(auc(null,observed),6),"primary_alpha":.05,"sensitivity":sensitivity}


def report(path: Path, payload: dict[str,Any]) -> None:
    s=payload["summary"]; lines=["# SDCR Matched Cross-Model Null", "", f"- Primary metric: {s['metric']}", f"- Null pairs: {s['null_pairs']:,}", f"- Cross-category pairs: {s['cross_view_pairs']:,}", f"- Null divergence mean/median: {s['null_mean']:.4f} / {s['null_median']:.4f}", f"- Cross-view divergence mean/median: {s['cross_mean']:.4f} / {s['cross_median']:.4f}", f"- AUROC (cross-view vs cross-model): {s['auroc']:.4f}", "", "| alpha | Null false-trigger | Cross-view trigger rate |", "|---:|---:|---:|"]
    for row in s["sensitivity"]: lines.append(f"| {row['alpha']:.3f} | {row['null_false_trigger']:.4f} | {row['cross_trigger_rate']:.4f} |")
    lines += ["", "The null holds category/profile fixed and varies only the generation model. It calibrates graph-observation divergence without answer labels. Answer-level utility remains a separate SDCR experiment.", ""]; path.write_text("\n".join(lines))


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=OUT);parser.add_argument("--reuse-cache",action="store_true");parser.add_argument("--entity-limit",type=int,default=50);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cross_payload=json.loads(CROSS.read_text());cross_rows=cross_payload["entity_context_divergence"]; names=[]
    for row in cross_rows:
        if row["entity"] not in names:names.append(row["entity"])
    names=set(names[:args.entity_limit]);cache=args.output/"provider_category_graphs.json.gz"
    if args.reuse_cache and cache.exists():
        with gzip.open(cache,"rt") as handle:saved=json.load(handle);graphs,audit=saved["graphs"],saved["audit"]
    else:
        graphs,audit=export_provider_graphs(names)
        with gzip.open(cache,"wt") as handle:json.dump({"graphs":graphs,"audit":audit},handle)
    retrieved=ppr_neighbors(graphs,names); null=null_rows(retrieved); selected_cross=[row for row in cross_rows if row["entity"] in names]; payload={"contract":"log2026.sdcr_matched_null.v1","audit":audit,"selection":"same output-blind entity list as full network analysis","summary":summarize(null,selected_cross),"null_rows":null}
    (args.output/"analysis.json").write_text(json.dumps(payload,indent=2)+"\n");report(args.output/"analysis.md",payload);print(args.output/"analysis.json");return 0


if __name__=="__main__":raise SystemExit(main())
