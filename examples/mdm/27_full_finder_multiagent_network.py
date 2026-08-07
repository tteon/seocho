#!/usr/bin/env python3
"""Full-FinDER network/GNN analysis for category-scoped graph agents.

Reads the completed survivorship profile from four DozerDB provider databases,
maps every workspace to its FinDER category, and collapses repeated observations
to a category-local semantic graph. Database access is read-only. Category,
provider, and QA labels are excluded from GNN features and the link objective.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FEDCAT = ROOT / "outputs/evaluation/mdm_fedcat"
PARTIALS = FEDCAT / "fedcat-full-all-survivorship-v1/index_partial"
OUT = FEDCAT / "log2026-full-multiagent-network-v1"
INFRA = {"Document", "DocumentVersion", "Section", "Chunk", "Memory", "SourceRef"}
STOP = {"the", "company", "entity", "policy", "risk", "revenue", "cost", "costs", "financial", "information", "data", "result", "results", "other", "none", "reporting company", "the company"}


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower())).strip()


def valid_identity(name: str) -> bool:
    return len(name) >= 3 and name not in STOP and not name.endswith(" unspecified")


def workspace_categories(path: Path) -> dict[str, str]:
    output = {}
    for item in sorted(path.glob("*.json")):
        row = json.loads(item.read_text(encoding="utf-8"))
        if not row.get("error"):
            output[str(row["workspace_id"])] = str(row["category"])
    return output


def _primary_label(labels: list[str]) -> str:
    business = sorted(label for label in labels if label not in INFRA)
    return business[0] if business else "Entity"


def export_collapsed_graph(partials: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    import yaml

    load_dotenv(ROOT / ".env")
    mapping = workspace_categories(partials)
    config = yaml.safe_load((ROOT / "examples/mdm/config/provider_databases.yaml").read_text())
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    raw_to_collapsed: dict[str, tuple[str, str]] = {}
    raw_nodes = raw_edges = 0
    for provider_id, spec in config["instances"].items():
        driver = GraphDatabase.driver(spec["uri"], auth=auth)
        try:
            with driver.session(database=spec["database"]) as session:
                result = session.run(
                    "MATCH (n) WHERE n._workspace_id STARTS WITH $prefix "
                    "RETURN elementId(n) AS id, n._workspace_id AS workspace, "
                    "coalesce(n.name, '') AS name, labels(n) AS labels",
                    prefix="fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-",
                )
                for row in result:
                    raw_nodes += 1
                    workspace = str(row["workspace"] or "")
                    category = mapping.get(workspace)
                    name = normalize(str(row["name"] or ""))
                    labels = list(row["labels"] or [])
                    if not category or not valid_identity(name) or set(labels) <= INFRA:
                        continue
                    key = (category, name)
                    node = collapsed.setdefault(key, {"category": category, "name": name, "labels": Counter(), "observations": 0})
                    node["labels"].update(label for label in labels if label not in INFRA)
                    node["observations"] += 1
                    raw_to_collapsed[f"{spec['database']}:{row['id']}"] = key
                result = session.run(
                    "MATCH (a)-[r]->(b) WHERE a._workspace_id STARTS WITH $prefix "
                    "RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS type",
                    prefix="fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-",
                )
                edge_counts: Counter[tuple[tuple[str, str], tuple[str, str], str]] = Counter()
                for row in result:
                    raw_edges += 1
                    source = raw_to_collapsed.get(f"{spec['database']}:{row['source']}")
                    target = raw_to_collapsed.get(f"{spec['database']}:{row['target']}")
                    if source and target and source[0] == target[0] and source != target:
                        edge_counts[(source, target, str(row["type"]))] += 1
                # Store per-provider observations; later providers add weights.
                if provider_id == next(iter(config["instances"])):
                    all_edges: Counter[Any] = Counter()
                all_edges.update(edge_counts)
        finally:
            driver.close()
    ordered_keys = sorted(collapsed)
    key_to_id = {key: index for index, key in enumerate(ordered_keys)}
    nodes = []
    for key in ordered_keys:
        node = collapsed[key]
        nodes.append({"id": key_to_id[key], "category": node["category"], "name": node["name"],
                      "labels": [label for label, _ in node["labels"].most_common()], "observations": node["observations"]})
    edges = [
        {"source": key_to_id[source], "target": key_to_id[target], "type": rel, "weight": weight, "category": source[0]}
        for (source, target, rel), weight in sorted(all_edges.items())
    ]
    audit = {"raw_nodes_seen": raw_nodes, "raw_edges_seen": raw_edges, "collapsed_nodes": len(nodes), "collapsed_edges": len(edges), "workspace_count": len(mapping), "read_only": True}
    return nodes, edges, audit


def graph_summaries(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    import networkx as nx
    output = {}
    for category in sorted({node["category"] for node in nodes}):
        ids = [node["id"] for node in nodes if node["category"] == category]
        graph = nx.DiGraph(); graph.add_nodes_from(ids)
        graph.add_weighted_edges_from((e["source"], e["target"], e["weight"]) for e in edges if e["category"] == category)
        undirected = graph.to_undirected()
        components = list(nx.connected_components(undirected))
        degree = [value for _, value in graph.degree()]
        pagerank = nx.pagerank(graph, weight="weight", max_iter=200, tol=1e-8)
        probabilities = list(pagerank.values())
        entropy = -sum(p * math.log2(p) for p in probabilities if p > 0)
        output[category] = {
            "nodes": graph.number_of_nodes(), "edges": graph.number_of_edges(),
            "density": round(nx.density(graph), 8), "weak_components": len(components),
            "largest_component_fraction": round(max(map(len, components), default=0) / max(len(ids), 1), 6),
            "mean_degree": round(mean(degree), 4) if degree else 0,
            "median_degree": round(median(degree), 4) if degree else 0,
            "reciprocity": round(float(nx.reciprocity(graph) or 0), 6),
            "transitivity": round(nx.transitivity(undirected), 6),
            "pagerank_entropy_normalized": round(entropy / math.log2(max(len(ids), 2)), 6),
            "pagerank_top1_share": round(max(probabilities, default=0), 8),
        }
    return output


def repeated_entity_pairs(nodes: list[dict[str, Any]], minimum_observations: int = 4, limit: int = 300) -> list[str]:
    categories: dict[str, set[str]] = defaultdict(set)
    observations: Counter[str] = Counter()
    for node in nodes:
        categories[node["name"]].add(node["category"]); observations[node["name"]] += node["observations"]
    eligible = [name for name, cats in categories.items() if len(cats) >= 2 and observations[name] >= minimum_observations]
    return sorted(eligible, key=lambda name: (-len(categories[name]), -observations[name], name))[:limit]


def local_and_ppr_divergence(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], names: list[str], top_k: int = 20) -> list[dict[str, Any]]:
    import networkx as nx
    by_id = {node["id"]: node for node in nodes}
    lookup = {(node["category"], node["name"]): node["id"] for node in nodes}
    graphs = {}
    typed_adj: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for edge in edges:
        typed_adj[edge["source"]].append((edge["target"], ">" + edge["type"])); typed_adj[edge["target"]].append((edge["source"], "<" + edge["type"]))
    for category in sorted({node["category"] for node in nodes}):
        graph = nx.DiGraph(); graph.add_nodes_from(node["id"] for node in nodes if node["category"] == category)
        for edge in edges:
            if edge["category"] == category:
                graph.add_edge(edge["source"], edge["target"], weight=edge["weight"]); graph.add_edge(edge["target"], edge["source"], weight=edge["weight"])
        graphs[category] = graph

    def paths(root: int, hops: int) -> Counter[str]:
        counter = Counter(); queue = deque([(root, tuple(), {root})])
        while queue:
            current, path, visited = queue.popleft()
            if len(path) == hops: continue
            for neighbor, rel in typed_adj[current]:
                if neighbor in visited: continue
                new = path + (rel,); label = (by_id[neighbor].get("labels") or ["Entity"])[0]
                counter["/".join(new) + "->" + label] += 1
                queue.append((neighbor, new, visited | {neighbor}))
        return counter

    def js(left: Counter[str], right: Counter[str]) -> float:
        keys = set(left) | set(right)
        if not keys: return 0.0
        lp, rp = sum(left.values()), sum(right.values())
        if not lp or not rp: return 1.0
        value = 0.0
        for key in keys:
            p, q = left[key] / lp, right[key] / rp; m = (p + q) / 2
            if p: value += 0.5 * p * math.log2(p / m)
            if q: value += 0.5 * q * math.log2(q / m)
        return value

    rows = []
    for name in names:
        categories = sorted(category for category in graphs if (category, name) in lookup)
        retrieval = {}; counters = {}
        for category in categories:
            root = lookup[(category, name)]; scores = nx.pagerank(graphs[category], alpha=.85, personalization={root: 1.0}, weight="weight", max_iter=200, tol=1e-8)
            top = [node_id for node_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0])) if node_id != root][:top_k]
            retrieval[category] = [by_id[node_id]["name"] for node_id in top]
            counters[category] = {2: paths(root, 2), 3: paths(root, 3)}
        for index, left in enumerate(categories):
            for right in categories[index + 1:]:
                lset, rset = set(retrieval[left]), set(retrieval[right]); union = lset | rset
                rows.append({"entity": name, "left_category": left, "right_category": right,
                             "ppr20_jaccard": round(len(lset & rset) / len(union), 6) if union else 1.0,
                             "ppr20_divergence": round(1 - len(lset & rset) / len(union), 6) if union else 0.0,
                             "two_hop_js": round(js(counters[left][2], counters[right][2]), 6),
                             "three_hop_js": round(js(counters[left][3], counters[right][3]), 6),
                             "left_top": retrieval[left][:10], "right_top": retrieval[right][:10]})
    return sorted(rows, key=lambda row: (-row["ppr20_divergence"], -row["three_hop_js"], row["entity"]))


def gnn_embeddings(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], epochs: int = 20) -> tuple[dict[int, list[float]], dict[str, Any]]:
    import torch
    import torch.nn.functional as F
    from torch_geometric.nn import RGCNConv
    from torch_geometric.utils import negative_sampling
    torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    width = 64; x = torch.zeros((len(nodes), width))
    for node in nodes:
        tokens = ["label:" + label for label in node["labels"]] + ["name:" + token for token in node["name"].split()]
        for token in tokens: x[node["id"], int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % width] += 1
    x = F.normalize(x, dim=1).to(device)
    rels = sorted({edge["type"] for edge in edges}); rel_idx = {rel: i for i, rel in enumerate(rels)}
    src = [edge["source"] for edge in edges]; dst = [edge["target"] for edge in edges]; types = [rel_idx[edge["type"]] for edge in edges]
    edge_index = torch.tensor([src + dst, dst + src], dtype=torch.long, device=device)
    edge_type = torch.tensor(types + [value + len(rels) for value in types], dtype=torch.long, device=device)
    positive = torch.tensor([src, dst], dtype=torch.long, device=device)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.a = RGCNConv(width, 48, len(rels) * 2, num_bases=min(12, len(rels) * 2)); self.b = RGCNConv(48, 32, len(rels) * 2, num_bases=min(12, len(rels) * 2))
        def forward(self): return self.b(F.relu(self.a(x, edge_index, edge_type)), edge_index, edge_type)
    model = Model().to(device); optimizer = torch.optim.Adam(model.parameters(), lr=.01); losses = []
    for _ in range(epochs):
        optimizer.zero_grad(); z = model(); negative = negative_sampling(positive, num_nodes=len(nodes), num_neg_samples=min(len(src), 200_000))
        pos = positive[:, torch.randperm(positive.size(1), device=device)[:min(positive.size(1), 200_000)]]
        loss = F.binary_cross_entropy_with_logits((z[pos[0]] * z[pos[1]]).sum(1), torch.ones(pos.size(1), device=device))
        loss += F.binary_cross_entropy_with_logits((z[negative[0]] * z[negative[1]]).sum(1), torch.zeros(negative.size(1), device=device))
        loss.backward(); optimizer.step(); losses.append(float(loss.detach().cpu()))
    with torch.no_grad(): z = F.normalize(model(), dim=1).cpu()
    return {node["id"]: z[node["id"]].tolist() for node in nodes}, {"device": str(device), "epochs": epochs, "final_loss": round(losses[-1], 6), "objective": "unsupervised relation-aware edge reconstruction", "features": "hashed entity labels and name tokens only; category/provider/QA labels excluded"}


def add_embedding(rows: list[dict[str, Any]], nodes: list[dict[str, Any]], vectors: dict[int, list[float]]) -> None:
    import torch
    lookup = {(node["category"], node["name"]): node["id"] for node in nodes}
    for row in rows:
        left = torch.tensor(vectors[lookup[(row["left_category"], row["entity"])]])
        right = torch.tensor(vectors[lookup[(row["right_category"], row["entity"])]])
        row["gnn_cosine"] = round(float(torch.dot(left, right)), 6); row["gnn_divergence"] = round(1 - row["gnn_cosine"], 6)


def svg_bars(path: Path, summaries: dict[str, Any]) -> None:
    cats = list(summaries); width, height = 980, 480; maximum = max(summaries[c]["nodes"] for c in cats)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>', '<text x="30" y="35" font-size="22" font-family="sans-serif">Category-agent graph size (full FinDER)</text>']
    for i, cat in enumerate(cats):
        y = 65 + i * 48; bar = 650 * summaries[cat]["nodes"] / maximum
        parts += [f'<text x="30" y="{y+20}" font-size="14" font-family="sans-serif">{cat}</text>', f'<rect x="210" y="{y}" width="{bar:.1f}" height="24" fill="#4c78a8"/>', f'<text x="{220+bar:.1f}" y="{y+18}" font-size="13" font-family="sans-serif">{summaries[cat]["nodes"]:,} nodes / {summaries[cat]["edges"]:,} edges</text>']
    parts.append('</svg>'); path.write_text("\n".join(parts))


def svg_divergence(path: Path, rows: list[dict[str, Any]]) -> None:
    metrics = [("PPR@20", "ppr20_divergence", "#4c78a8"), ("2-hop typed paths", "two_hop_js", "#f58518"), ("3-hop typed paths", "three_hop_js", "#54a24b")]
    width, height = 980, 460; parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>', '<text x="30" y="35" font-size="22" font-family="sans-serif">Cross-category entity-context divergence</text>', '<text x="30" y="60" font-size="13" font-family="sans-serif">Distribution over output-blind repeated-entity view pairs; higher means more different observations</text>']
    for metric_index, (label, field, color) in enumerate(metrics):
        counts = [0] * 10
        for row in rows: counts[min(int(float(row[field]) * 10), 9)] += 1
        maximum = max(counts) or 1; base_y = 175 + metric_index * 105
        parts.append(f'<text x="30" y="{base_y-50}" font-size="15" font-family="sans-serif" fill="{color}">{label}</text>')
        for index, count in enumerate(counts):
            bar_height = 55 * count / maximum; x = 210 + index * 68
            parts += [f'<rect x="{x}" y="{base_y-bar_height:.1f}" width="54" height="{bar_height:.1f}" fill="{color}" opacity="0.85"/>', f'<text x="{x+8}" y="{base_y+18}" font-size="11" font-family="sans-serif">{index/10:.1f}</text>']
    parts.append('</svg>'); path.write_text("\n".join(parts))


def write_report(path: Path, payload: dict[str, Any]) -> None:
    rows = payload["entity_context_divergence"]
    avg = lambda field: mean(float(row[field]) for row in rows) if rows else 0
    gnn_available = bool(rows and "gnn_divergence" in rows[0])
    lines = ["# Full FinDER Multi-Agent Network Analysis", "", f"- Raw graph: {payload['audit']['raw_nodes_seen']:,} nodes, {payload['audit']['raw_edges_seen']:,} relationships", f"- Collapsed category-agent graph: {payload['audit']['collapsed_nodes']:,} nodes, {payload['audit']['collapsed_edges']:,} typed edges", f"- Repeated entities selected output-blind: {payload['repeated_entity_count']}", f"- Cross-category entity pairs: {len(rows):,}", f"- Mean PPR@20 divergence: {avg('ppr20_divergence'):.4f}", f"- Mean 2-hop JS divergence: {avg('two_hop_js'):.4f}", f"- Mean 3-hop JS divergence: {avg('three_hop_js'):.4f}", f"- GNN status: {'completed' if gnn_available else 'deferred until the NVIDIA driver is repaired'}", "", "## Category graph summary", "", "| Category agent | Nodes | Edges | Mean degree | Largest component | Reciprocity | Transitivity | PR entropy |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for cat, item in payload["category_graphs"].items(): lines.append(f"| {cat} | {item['nodes']:,} | {item['edges']:,} | {item['mean_degree']:.3f} | {item['largest_component_fraction']:.3f} | {item['reciprocity']:.3f} | {item['transitivity']:.3f} | {item['pagerank_entropy_normalized']:.3f} |")
    lines += ["", "## Highest-divergence shared entities", "", "| Entity | Views | PPR divergence | 2-hop JS | 3-hop JS | GNN divergence |", "|---|---|---:|---:|---:|---:|"]
    for row in rows[:30]: lines.append(f"| `{row['entity']}` | {row['left_category']} ↔ {row['right_category']} | {row['ppr20_divergence']:.3f} | {row['two_hop_js']:.3f} | {row['three_hop_js']:.3f} | {row.get('gnn_divergence', 'deferred')} |")
    lines += ["", "## Multi-agent interpretation", "", "The same normalized entity induces different retrievable neighborhoods in category-scoped graphs. PPR is the retrieval-facing signal and typed-path JS explains which relation contexts changed. GNN is explicitly deferred while the NVIDIA driver is inconsistent. These network metrics establish observation diversity, not answer improvement. Coalition benefit still requires slot and answer evaluation under fixed budgets.", ""]
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=OUT); parser.add_argument("--entity-limit", type=int, default=300); parser.add_argument("--epochs", type=int, default=20); parser.add_argument("--reuse-cache", action="store_true"); parser.add_argument("--skip-gnn", action="store_true"); args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True); cache = args.output / "collapsed_graph.json.gz"
    if args.reuse_cache and cache.exists():
        with gzip.open(cache, "rt") as handle: saved = json.load(handle); nodes, edges, audit = saved["nodes"], saved["edges"], saved["audit"]
    else:
        nodes, edges, audit = export_collapsed_graph(PARTIALS)
        with gzip.open(cache, "wt") as handle: json.dump({"nodes": nodes, "edges": edges, "audit": audit}, handle)
    summaries = graph_summaries(nodes, edges); names = repeated_entity_pairs(nodes, limit=args.entity_limit); rows = local_and_ppr_divergence(nodes, edges, names)
    training: dict[str, Any] = {"status": "deferred"}
    if not args.skip_gnn:
        vectors, training = gnn_embeddings(nodes, edges, epochs=args.epochs); add_embedding(rows, nodes, vectors)
    payload = {"contract": "log2026.full_multiagent_network.v1", "audit": audit, "selection": "entities ranked by number of represented categories, then observation count, then lexical name; no answer/model score used", "repeated_entity_count": len(names), "category_graphs": summaries, "gnn_training": training, "entity_context_divergence": rows}
    (args.output / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n"); write_report(args.output / "analysis.md", payload); svg_bars(args.output / "category_graph_sizes.svg", summaries); svg_divergence(args.output / "entity_context_divergence.svg", rows); print(args.output / "analysis.json"); return 0


if __name__ == "__main__": raise SystemExit(main())
