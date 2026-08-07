#!/usr/bin/env python3
"""Quantify cross-category context divergence without modifying DozerDB.

Exports category graphs with read-only Cypher, measures 1/2/3-hop typed-path
divergence for repeated entity keys, and optionally trains an unsupervised PyG
R-GCN link-reconstruction encoder. Category and QA labels are excluded from GNN
features and loss.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATEGORY_DATABASES = {
    "Accounting": "mdmcataccounting",
    "Company overview": "mdmcatcompany",
    "Financials": "mdmcatfinancials",
    "Footnotes": "mdmcatfootnotes",
    "Governance": "mdmcatgovernance",
    "Legal": "mdmcatlegal",
    "Risk": "mdmcatrisk",
    "Shareholder return": "mdmcatshareholder",
}
STOP_NAMES = {
    "the", "company", "entity", "policy", "risk", "revenue", "cost", "costs",
    "financial", "information", "data", "result", "results", "other", "none",
    "reportingcompany",
}
INFRA_LABELS={"Document","DocumentVersion","Section","Chunk","Memory","SourceRef"}
GENERIC_NAME_PATTERNS = (
    re.compile(r"^(?:the |reporting |filing )?company(?: unspecified)?$"),
    re.compile(r"^(?:the )?(?:registrant|issuer|group|organization)$"),
)


def normalize_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower())).strip()


def context_key(node: dict[str, Any]) -> str:
    name = normalize_name(str(node.get("name") or ""))
    label = str((node.get("labels") or ["Entity"])[0])
    return f"{name}|{label}" if name else ""


def is_identity_candidate(key: str) -> bool:
    if not key:
        return False
    name = key.split("|", 1)[0]
    return len(name) >= 3 and name not in STOP_NAMES and not any(
        pattern.fullmatch(name) for pattern in GENERIC_NAME_PATTERNS
    )


def normalize_modules(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item).strip() for item in value if str(item).strip()})
    raw=str(value or "").strip().strip("[]")
    return sorted({item.strip().strip("'\"") for item in raw.split(",") if item.strip().strip("'\"")})


def jensen_shannon(left: Counter[str], right: Counter[str]) -> float:
    keys = sorted(set(left) | set(right))
    if not keys:
        return 0.0
    l_total, r_total = sum(left.values()), sum(right.values())
    if not l_total or not r_total:
        return 1.0
    p = [left[key] / l_total for key in keys]
    q = [right[key] / r_total for key in keys]
    m = [(a + b) / 2 for a, b in zip(p, q)]

    def kl(xs: list[float], ys: list[float]) -> float:
        return sum(x * math.log2(x / y) for x, y in zip(xs, ys) if x > 0)

    return round((kl(p, m) + kl(q, m)) / 2, 6)


def _read_graphs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    try:
        for category, database in CATEGORY_DATABASES.items():
            with driver.session(database=database) as session:
                for row in session.run(
                    "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, "
                    "coalesce(n.name, '') AS name, coalesce(n.case_id, '') AS case_id, "
                    "coalesce(n.provider_id, '') AS provider_id, coalesce(n.model, '') AS model, "
                    "coalesce(n.prompt_id, '') AS prompt_id, coalesce(n.ontology_hash, '') AS ontology_hash, "
                    "coalesce(n.ontology_modules, []) AS ontology_modules"
                ):
                    nodes.append({"id": f"{database}:{row['id']}", "category": category,
                                  "database": database, "labels": list(row["labels"] or []),
                                  "name": str(row["name"] or ""), "case_id": str(row["case_id"] or ""),
                                  "provider_id": str(row["provider_id"] or ""),
                                  "model": str(row["model"] or ""), "prompt_id": str(row["prompt_id"] or ""),
                                  "ontology_hash": str(row["ontology_hash"] or ""),
                                  "ontology_modules": normalize_modules(row["ontology_modules"])})
                for row in session.run(
                    "MATCH (a)-[r]->(b) RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS type"
                ):
                    edges.append({"source": f"{database}:{row['source']}",
                                  "target": f"{database}:{row['target']}",
                                  "type": str(row["type"]), "category": category})
    finally:
        driver.close()
    return nodes, edges


def _path_counters(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], max_hops: int = 3
) -> dict[tuple[str, str], dict[int, Counter[str]]]:
    by_id = {node["id"]: node for node in nodes}
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["source"]].append((edge["target"], f">{edge['type']}"))
        adjacency[edge["target"]].append((edge["source"], f"<{edge['type']}"))
    output: dict[tuple[str, str], dict[int, Counter[str]]] = defaultdict(
        lambda: {hop: Counter() for hop in range(1, max_hops + 1)}
    )
    for node in nodes:
        key = context_key(node)
        name = key.split("|", 1)[0] if key else ""
        if not is_identity_candidate(key):
            continue
        queue = deque([(node["id"], tuple())])
        visited = {(node["id"], tuple())}
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for neighbor, rel in adjacency.get(current, []):
                new_path = path + (rel,)
                state = (neighbor, new_path)
                if state in visited:
                    continue
                visited.add(state)
                terminal = str((by_id.get(neighbor, {}).get("labels") or ["Entity"])[0])
                signature = "/".join(new_path) + f"->{terminal}"
                output[(key, node["category"])][len(new_path)][signature] += 1
                queue.append((neighbor, new_path))
    return output


def structural_divergence(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    counters = _path_counters(nodes, edges)
    categories_by_key: dict[str, set[str]] = defaultdict(set)
    for key, category in counters:
        categories_by_key[key].add(category)
    rows: list[dict[str, Any]] = []
    for key, categories in categories_by_key.items():
        if len(categories) < 2:
            continue
        ordered = sorted(categories)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                hop_js = {
                    str(hop): jensen_shannon(counters[(key, left)][hop], counters[(key, right)][hop])
                    for hop in (1, 2, 3)
                }
                rows.append({
                    "context_key": key, "left_category": left, "right_category": right,
                    "hop_js_divergence": hop_js,
                    "mean_hop_divergence": round(sum(hop_js.values()) / 3, 6),
                    "left_top_paths": {str(h): counters[(key, left)][h].most_common(3) for h in (1, 2, 3)},
                    "right_top_paths": {str(h): counters[(key, right)][h].most_common(3) for h in (1, 2, 3)},
                })
    return sorted(rows, key=lambda row: row["mean_hop_divergence"], reverse=True)


def _hash_features(nodes: list[dict[str, Any]], width: int = 128):
    import torch

    x = torch.zeros((len(nodes), width), dtype=torch.float32)
    for index, node in enumerate(nodes):
        tokens = [f"label:{label}" for label in node.get("labels") or []]
        tokens.extend(f"name:{token}" for token in normalize_name(node.get("name", "")).split())
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            slot = int.from_bytes(digest[:4], "big") % width
            x[index, slot] += 1.0
        norm = x[index].norm()
        if norm:
            x[index] /= norm
    return x


def unsupervised_rgcn_embeddings(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, epochs: int = 100, seed: int = 42
) -> tuple[list[list[float]], dict[str, Any]]:
    import torch
    import torch.nn.functional as F
    from torch_geometric.nn import RGCNConv
    from torch_geometric.utils import negative_sampling

    random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    index = {node["id"]: i for i, node in enumerate(nodes)}
    rel_names = sorted({edge["type"] for edge in edges})
    rel_index = {name: i for i, name in enumerate(rel_names)}
    src = [index[e["source"]] for e in edges if e["source"] in index and e["target"] in index]
    dst = [index[e["target"]] for e in edges if e["source"] in index and e["target"] in index]
    types = [rel_index[e["type"]] for e in edges if e["source"] in index and e["target"] in index]
    edge_index = torch.tensor([src + dst, dst + src], dtype=torch.long, device=device)
    edge_type = torch.tensor(types + [t + len(rel_names) for t in types], dtype=torch.long, device=device)
    x = _hash_features(nodes).to(device)

    class Encoder(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.c1 = RGCNConv(x.size(1), 96, len(rel_names) * 2, num_bases=min(16, len(rel_names) * 2))
            self.c2 = RGCNConv(96, 64, len(rel_names) * 2, num_bases=min(16, len(rel_names) * 2))
        def forward(self):
            return self.c2(F.relu(self.c1(x, edge_index, edge_type)), edge_index, edge_type)

    model = Encoder().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-5)
    losses: list[float] = []
    positive = torch.tensor([src, dst], dtype=torch.long, device=device)
    for _ in range(epochs):
        model.train(); optimizer.zero_grad(); z = model()
        negative = negative_sampling(positive, num_nodes=len(nodes), num_neg_samples=len(src))
        pos_score = (z[positive[0]] * z[positive[1]]).sum(dim=1)
        neg_score = (z[negative[0]] * z[negative[1]]).sum(dim=1)
        loss = F.binary_cross_entropy_with_logits(pos_score, torch.ones_like(pos_score))
        loss += F.binary_cross_entropy_with_logits(neg_score, torch.zeros_like(neg_score))
        loss.backward(); optimizer.step(); losses.append(float(loss.detach().cpu()))
    model.eval()
    with torch.no_grad():
        z = F.normalize(model(), dim=1).cpu()
    return z.tolist(), {"device": str(device), "epochs": epochs, "final_loss": round(losses[-1], 6),
                       "node_feature_contract": "hashed labels+name tokens; category/provider/QA labels excluded",
                       "objective": "self-supervised edge reconstruction"}


def embedding_divergence(nodes: list[dict[str, Any]], vectors: list[list[float]]) -> list[dict[str, Any]]:
    import torch
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        key = context_key(node); name = key.split("|", 1)[0] if key else ""
        if is_identity_candidate(key):
            grouped[(key, node["category"])].append(index)
    by_key: dict[str, list[str]] = defaultdict(list)
    for key, category in grouped:
        by_key[key].append(category)
    tensor = torch.tensor(vectors)
    rows = []
    for key, categories in by_key.items():
        ordered = sorted(set(categories))
        for i, left in enumerate(ordered):
            lv = tensor[grouped[(key, left)]].mean(0); lv = lv / lv.norm().clamp_min(1e-9)
            for right in ordered[i + 1:]:
                rv = tensor[grouped[(key, right)]].mean(0); rv = rv / rv.norm().clamp_min(1e-9)
                rows.append({"context_key": key, "left_category": left, "right_category": right,
                             "cosine_similarity": round(float(torch.dot(lv, rv)), 6),
                             "embedding_divergence": round(1 - float(torch.dot(lv, rv)), 6)})
    return sorted(rows, key=lambda row: row["embedding_divergence"], reverse=True)


def provenance_profiles(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    profiles: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"providers": set(), "models": set(), "prompt_ids": set(),
                 "ontology_hashes": set(), "ontology_modules": set()}
    )
    for node in nodes:
        profile = profiles[node["category"]]
        for field, target in (("provider_id", "providers"), ("model", "models"),
                              ("prompt_id", "prompt_ids"), ("ontology_hash", "ontology_hashes")):
            value = str(node.get(field) or "").strip()
            if value:
                profile[target].add(value)
        profile["ontology_modules"].update(str(v) for v in node.get("ontology_modules") or [] if v)
    return {category: {key: sorted(values) for key, values in profile.items()}
            for category, profile in sorted(profiles.items())}


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    ordered=sorted(values.items(),key=lambda item:(item[1],item[0]))
    denominator=max(len(ordered)-1,1)
    return {node_id:rank/denominator for rank,(node_id,_) in enumerate(ordered)}


def network_science_divergence(nodes: list[dict[str,Any]], edges: list[dict[str,Any]]) -> list[dict[str,Any]]:
    import networkx as nx
    by_id={node["id"]:node for node in nodes}
    category_metrics: dict[str,dict[str,dict[str,float]]]={}
    for category in CATEGORY_DATABASES:
        ids=[node["id"] for node in nodes if node["category"]==category]
        graph=nx.DiGraph(); graph.add_nodes_from(ids)
        graph.add_edges_from((e["source"],e["target"]) for e in edges if e["category"]==category)
        pagerank=nx.pagerank(graph,max_iter=200,tol=1e-8)
        degree={node:float(graph.in_degree(node)+graph.out_degree(node)) for node in graph}
        # Exact betweenness is tractable for the current <2k-node category graphs.
        betweenness=nx.betweenness_centrality(graph,k=min(128,len(graph)),seed=42,normalized=True)
        clustering=nx.clustering(graph.to_undirected())
        category_metrics[category]={
            "pagerank":_percentiles(pagerank),"degree":_percentiles(degree),
            "betweenness":_percentiles(betweenness),"clustering":clustering,
        }
    grouped: dict[tuple[str,str],list[str]]=defaultdict(list)
    for node in nodes:
        key=context_key(node)
        if is_identity_candidate(key): grouped[(key,node["category"])].append(node["id"])
    by_key: dict[str,set[str]]=defaultdict(set)
    for key,category in grouped: by_key[key].add(category)
    rows=[]
    for key,categories in by_key.items():
        ordered=sorted(categories)
        for i,left in enumerate(ordered):
            for right in ordered[i+1:]:
                def aggregate(category: str, metric: str)->float:
                    vals=[category_metrics[category][metric].get(node_id,0.0) for node_id in grouped[(key,category)]]
                    return sum(vals)/len(vals) if vals else 0.0
                lp, rp=aggregate(left,"pagerank"),aggregate(right,"pagerank")
                ld, rd=aggregate(left,"degree"),aggregate(right,"degree")
                lb, rb=aggregate(left,"betweenness"),aggregate(right,"betweenness")
                lc, rc=aggregate(left,"clustering"),aggregate(right,"clustering")
                shift=(abs(lp-rp)+abs(ld-rd)+abs(lb-rb)+abs(lc-rc))/4
                rows.append({"context_key":key,"left_category":left,"right_category":right,
                  "left":{"pagerank_percentile":round(lp,6),"degree_percentile":round(ld,6),
                          "betweenness_percentile":round(lb,6),"clustering":round(lc,6)},
                  "right":{"pagerank_percentile":round(rp,6),"degree_percentile":round(rd,6),
                           "betweenness_percentile":round(rb,6),"clustering":round(rc,6)},
                  "role_shift_score":round(shift,6)})
    return sorted(rows,key=lambda row:row["role_shift_score"],reverse=True)


def personalized_pagerank_retrieval(
    nodes: list[dict[str,Any]], edges: list[dict[str,Any]], *, top_k: int = 20
) -> list[dict[str,Any]]:
    """Compare query-entity-seeded PPR retrieval across category graphs."""
    import networkx as nx
    by_id={node["id"]:node for node in nodes}
    roots: dict[tuple[str,str],list[str]]=defaultdict(list)
    for node in nodes:
        key=context_key(node)
        if is_identity_candidate(key): roots[(key,node["category"])].append(node["id"])
    categories_by_key: dict[str,set[str]]=defaultdict(set)
    for key,category in roots: categories_by_key[key].add(category)
    graphs={}
    for category in CATEGORY_DATABASES:
        graph=nx.DiGraph()
        graph.add_nodes_from(node["id"] for node in nodes if node["category"]==category)
        # Reverse edges permit evidence expansion in either stored direction.
        for edge in edges:
            if edge["category"]==category:
                graph.add_edge(edge["source"],edge["target"])
                graph.add_edge(edge["target"],edge["source"])
        graphs[category]=graph
    retrieved: dict[tuple[str,str],list[dict[str,Any]]]={}
    for (key,category),seed_ids in roots.items():
        if len(categories_by_key[key])<2: continue
        graph=graphs[category]
        personalization={node_id:1.0/len(seed_ids) for node_id in seed_ids}
        scores=nx.pagerank(graph,alpha=0.85,personalization=personalization,max_iter=200,tol=1e-8)
        collapsed: dict[str,dict[str,Any]]={}
        for node_id,score in scores.items():
            if node_id in seed_ids: continue
            node=by_id[node_id]; labels=set(node.get("labels") or [])
            if labels & INFRA_LABELS: continue
            neighbor_key=context_key(node)
            if not neighbor_key: continue
            existing=collapsed.get(neighbor_key)
            if existing is None or score>existing["score"]:
                collapsed[neighbor_key]={"neighbor_key":neighbor_key,"name":node.get("name",""),
                                         "labels":node.get("labels",[]),"score":float(score)}
        ordered=sorted(collapsed.values(),key=lambda row:(-row["score"],row["neighbor_key"]))[:top_k]
        for rank,row in enumerate(ordered,1): row["rank"]=rank; row["score"]=round(row["score"],8)
        retrieved[(key,category)]=ordered
    rows=[]
    for key,categories in categories_by_key.items():
        ordered=sorted(categories)
        for i,left in enumerate(ordered):
            for right in ordered[i+1:]:
                lrows=retrieved.get((key,left),[]); rrows=retrieved.get((key,right),[])
                lrank={row["neighbor_key"]:row["rank"] for row in lrows}
                rrank={row["neighbor_key"]:row["rank"] for row in rrows}
                union=set(lrank)|set(rrank); common=set(lrank)&set(rrank)
                jaccard=len(common)/len(union) if union else 1.0
                rank_shift=(sum(abs(lrank[n]-rrank[n])/(top_k-1 or 1) for n in common)/len(common)
                            if common else 1.0)
                rows.append({"context_key":key,"left_category":left,"right_category":right,"top_k":top_k,
                             "top_k_jaccard":round(jaccard,6),"top_k_divergence":round(1-jaccard,6),
                             "common_neighbor_count":len(common),"mean_normalized_rank_shift":round(rank_shift,6),
                             "left_top_neighbors":lrows[:10],"right_top_neighbors":rrows[:10]})
    return sorted(rows,key=lambda row:(row["top_k_divergence"],row["mean_normalized_rank_shift"]),reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-run-prefix", default="log2026-category-context-v1")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--skip-gnn", action="store_true")
    args = parser.parse_args()
    nodes, edges = _read_graphs()
    structural = structural_divergence(nodes, edges)
    network_science = network_science_divergence(nodes, edges)
    ppr = personalized_pagerank_retrieval(nodes, edges, top_k=20)
    payload: dict[str, Any] = {"contract": "log2026.category_context.v1", "read_only_source": True,
                               "node_count": len(nodes), "edge_count": len(edges),
                               "cross_category_pairs": len(structural),
                               "provenance_profiles": provenance_profiles(nodes),
                               "factor_scope": {
                                   "measured": "category-context divergence within the active snapshot",
                                   "not_identified": "causal prompt/ontology effects require matched snapshot reruns",
                               },
                               "network_science": network_science,
                               "ppr_retrieval": ppr,
                               "structural": structural}
    if not args.skip_gnn:
        vectors, training = unsupervised_rgcn_embeddings(nodes, edges, epochs=args.epochs)
        payload["gnn_training"] = training
        payload["embedding"] = embedding_divergence(nodes, vectors)
    out = ROOT / "outputs/evaluation/mdm_fedcat" / args.output_run_prefix
    out.mkdir(parents=True, exist_ok=True)
    (out / "context_divergence.json").write_text(json.dumps(payload, indent=2) + "\n")
    summary = ["# Cross-Category Entity Context Divergence", "",
               f"- Nodes: {len(nodes)}", f"- Edges: {len(edges)}",
               f"- Repeated entity/category pairs: {len(structural)}",
               "- Factor scope: category divergence within one active prompt/ontology snapshot", "",
               "| Entity key | Categories | PPR@20 overlap | PPR rank shift | 2-hop JS | 3-hop JS | GNN divergence |",
               "|---|---|---:|---:|---:|---:|---:|"]
    embed = {(r["context_key"],r["left_category"],r["right_category"]):r for r in payload.get("embedding",[])}
    pprs = {(r["context_key"],r["left_category"],r["right_category"]):r for r in ppr}
    for row in structural[:30]:
        er=embed.get((row["context_key"],row["left_category"],row["right_category"]),{})
        pr=pprs.get((row["context_key"],row["left_category"],row["right_category"]),{})
        h=row["hop_js_divergence"]
        summary.append(f"| `{row['context_key']}` | {row['left_category']} ↔ {row['right_category']} | {1-float(pr.get('top_k_divergence',0)):.3f} | {float(pr.get('mean_normalized_rank_shift',0)):.3f} | {h['2']:.3f} | {h['3']:.3f} | {float(er.get('embedding_divergence',0)):.3f} |")
    (out / "context_divergence.md").write_text("\n".join(summary)+"\n")
    print(f"wrote {out.relative_to(ROOT)}/context_divergence.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
