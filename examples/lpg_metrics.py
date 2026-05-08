"""Structure-based graph metrics for LPG side of Tutorial 3.

Pulls a workspace's nodes and relationships from a ``Neo4jGraphStore``
into a NetworkX ``DiGraph`` and computes a small panel of network
metrics: degree distribution, density, average clustering, PageRank
top-N, and weakly connected component count.

These are the kinds of *structure-based evaluation* signals that LPG
makes easy and RDF doesn't (without first projecting to a property
graph), which is why the tutorial scores this track LPG-only.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List


def _fetch_subgraph(graph_store: Any, *, database: str, workspace_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch nodes + edges for a given workspace via Cypher."""
    nodes = graph_store.query(
        "MATCH (n) WHERE n._workspace_id = $workspace_id "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
        params={"workspace_id": workspace_id},
        database=database,
    )
    edges = graph_store.query(
        "MATCH (a)-[r]->(b) WHERE r._workspace_id = $workspace_id "
        "RETURN a.id AS source, b.id AS target, type(r) AS type",
        params={"workspace_id": workspace_id},
        database=database,
    )
    return {"nodes": nodes, "edges": edges}


def compute_lpg_structure_metrics(
    graph_store: Any,
    *,
    database: str = "neo4j",
    workspace_id: str = "default",
    pagerank_top_n: int = 5,
) -> Dict[str, Any]:
    """Return a dict of network metrics for the given workspace.

    Requires NetworkX (``pip install networkx``).
    """
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError(
            "compute_lpg_structure_metrics requires 'networkx'. "
            "Install it with: pip install networkx"
        ) from exc

    payload = _fetch_subgraph(graph_store, database=database, workspace_id=workspace_id)
    g = nx.DiGraph()
    for n in payload["nodes"]:
        node_id = n.get("id") or ""
        labels = n.get("labels", []) or []
        primary_label = labels[0] if labels else "Entity"
        g.add_node(node_id, label=primary_label)
    for e in payload["edges"]:
        src = e.get("source")
        tgt = e.get("target")
        if src and tgt:
            g.add_edge(src, tgt, type=e.get("type", "RELATED_TO"))

    if g.number_of_nodes() == 0:
        return {
            "node_count": 0,
            "edge_count": 0,
            "density": 0.0,
            "weakly_connected_components": 0,
            "average_clustering": 0.0,
            "degree_histogram": {},
            "in_degree_histogram": {},
            "out_degree_histogram": {},
            "pagerank_top": [],
        }

    degree_hist = Counter(d for _, d in g.degree())
    in_hist = Counter(d for _, d in g.in_degree())
    out_hist = Counter(d for _, d in g.out_degree())

    try:
        avg_clustering = nx.average_clustering(g.to_undirected())
    except Exception:
        avg_clustering = 0.0

    try:
        pr = nx.pagerank(g)
        pr_sorted = sorted(pr.items(), key=lambda kv: kv[1], reverse=True)[:pagerank_top_n]
        pagerank_top = [{"node": k, "score": float(v)} for k, v in pr_sorted]
    except Exception:
        pagerank_top = []

    return {
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "density": float(nx.density(g)),
        "weakly_connected_components": int(nx.number_weakly_connected_components(g)),
        "average_clustering": float(avg_clustering),
        "degree_histogram": dict(sorted(degree_hist.items())),
        "in_degree_histogram": dict(sorted(in_hist.items())),
        "out_degree_histogram": dict(sorted(out_hist.items())),
        "pagerank_top": pagerank_top,
    }
