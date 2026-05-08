"""Graph-visualization helpers used across the FinDER tutorials.

Two entry points:

- ``draw_lpg(nodes, relationships, *, title=...)`` — draws a labeled
  property graph from the seocho-shaped node/rel dicts (or rows pulled
  back from a Neo4j query). Node colors group by label.
- ``fetch_lpg_subgraph(graph_store, *, workspace_id, database, limit)``
  — pulls a small subgraph back from a Neo4j-shaped GraphStore so the
  notebook doesn't have to write its own Cypher.

Both return data structures the notebook can pass to matplotlib.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _node_color_map(labels: Iterable[str]):
    import matplotlib.pyplot as plt

    distinct = sorted({l or "Unknown" for l in labels})
    palette = plt.cm.tab20.colors
    return {label: palette[i % len(palette)] for i, label in enumerate(distinct)}


def draw_lpg(
    nodes: Sequence[Dict[str, Any]],
    relationships: Sequence[Dict[str, Any]],
    *,
    title: str = "",
    max_nodes: int = 60,
    figsize: Tuple[int, int] = (12, 8),
    seed: int = 42,
):
    """Draw a labeled property graph from seocho node/rel payloads.

    Each ``node`` dict needs at minimum ``id`` and ``label``; optional
    ``properties.name`` is used for the on-screen label. Each
    ``relationship`` dict needs ``source``, ``target``, ``type``.
    """
    import matplotlib.pyplot as plt
    import networkx as nx

    g = nx.DiGraph()
    for n in list(nodes)[:max_nodes]:
        node_id = n.get("id") or n.get("properties", {}).get("name", "")
        if not node_id:
            continue
        label = str(n.get("label", "Unknown"))
        name = str(n.get("properties", {}).get("name", node_id))
        g.add_node(str(node_id), label=label, name=name)
    for r in relationships:
        s = str(r.get("source", ""))
        t = str(r.get("target", ""))
        if g.has_node(s) and g.has_node(t):
            g.add_edge(s, t, type=str(r.get("type", "REL")))

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    if g.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "(empty graph)", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        ax.axis("off")
        return fig

    color_map = _node_color_map(d["label"] for _, d in g.nodes(data=True))
    pos = nx.spring_layout(g, seed=seed, k=0.6)
    nx.draw_networkx_nodes(
        g,
        pos,
        node_color=[color_map[d["label"]] for _, d in g.nodes(data=True)],
        node_size=900,
        alpha=0.92,
        ax=ax,
    )
    nx.draw_networkx_labels(
        g,
        pos,
        labels={n: (d["name"] or n)[:20] for n, d in g.nodes(data=True)},
        font_size=8,
        ax=ax,
    )
    nx.draw_networkx_edges(g, pos, alpha=0.45, arrows=True, width=1.0, ax=ax)
    nx.draw_networkx_edge_labels(
        g,
        pos,
        edge_labels=nx.get_edge_attributes(g, "type"),
        font_size=6,
        ax=ax,
    )
    legend_handles = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=color, markersize=10, label=label)
        for label, color in color_map.items()
    ]
    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper left", fontsize=8, framealpha=0.85)
    ax.set_title(title or f"Property graph — {g.number_of_nodes()} nodes / {g.number_of_edges()} edges")
    ax.axis("off")
    fig.tight_layout()
    return fig


def fetch_lpg_subgraph(
    graph_store: Any,
    *,
    workspace_id: str,
    database: str = "neo4j",
    limit: int = 100,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pull a (nodes, relationships) snapshot back from a Neo4j-shaped store.

    Notebook usage::

        nodes, rels = fetch_lpg_subgraph(store, workspace_id="finder_tutorial")
        draw_lpg(nodes, rels, title="...")
    """
    node_rows = graph_store.query(
        "MATCH (n) WHERE n._workspace_id = $workspace_id "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props "
        "LIMIT $limit",
        params={"workspace_id": workspace_id, "limit": limit},
        database=database,
    )
    rel_rows = graph_store.query(
        "MATCH (a)-[r]->(b) WHERE r._workspace_id = $workspace_id "
        "RETURN a.id AS source, b.id AS target, type(r) AS type "
        "LIMIT $limit",
        params={"workspace_id": workspace_id, "limit": limit},
        database=database,
    )
    nodes = [
        {
            "id": row.get("id"),
            "label": (row.get("labels") or ["Entity"])[0],
            "properties": row.get("props") or {},
        }
        for row in node_rows
        if row.get("id")
    ]
    relationships = [
        {"source": row.get("source"), "target": row.get("target"), "type": row.get("type")}
        for row in rel_rows
        if row.get("source") and row.get("target")
    ]
    return nodes, relationships


