"""LanceDB-backed property graph store for the FinDER tutorial.

This is a small, tutorial-scoped adapter that implements the
``seocho.store.graph.GraphStore`` ABC over two LanceDB tables (``nodes``
and ``edges``). It is *not* a production graph backend.

Why this exists
---------------
LadybugDB (the embedded backend) does not support vector indexing. For a
fully Lance-based RAG pipeline we want both the vector store *and* the
graph store on Lance. Upstream lance-graph (a property-graph format on
top of Lance) is in development:

    https://github.com/lance-format/lance-graph/issues/91

When that ships, the swap is one import: replace this class with the
upstream ``LanceGraphStore`` and the rest of the tutorial keeps working.

What it supports
----------------
- ``write`` of nodes and relationships (always idempotent on ``id``).
- ``count_by_source`` / ``delete_by_source`` for provenance hygiene.
- ``ensure_constraints`` as a no-op returning a success summary.
- ``get_schema`` which returns labels and rel types observed so far.
- ``query`` with a *minimal* keyword fallback (Cypher is not parsed —
  we extract names from the cypher string and do substring lookup).
  Tutorials should prefer ``keyword_retrieve()`` and ``neighbors()``.
- ``execute_write`` is unimplemented (the tutorial uses ``write`` only).

The narrow scope keeps the adapter readable; the public entry points
the tutorial actually uses are ``write``, ``keyword_retrieve``, and
``neighbors``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from seocho.store.graph import GraphStore


_NAME_FROM_CYPHER_RE = re.compile(r"['\"]([^'\"]+)['\"]")


class LanceGraphStore(GraphStore):
    """Tutorial-only property graph backed by two LanceDB tables."""

    def __init__(
        self,
        *,
        uri: str = "./.seocho/finder_graph.lance",
        nodes_table: str = "nodes",
        edges_table: str = "edges",
    ) -> None:
        try:
            import lancedb
        except ImportError as exc:
            raise ImportError(
                "LanceGraphStore requires 'lancedb'. "
                "Install it with: pip install lancedb"
            ) from exc

        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        self._lancedb = lancedb
        self._db = lancedb.connect(uri)
        self._nodes_table_name = nodes_table
        self._edges_table_name = edges_table
        self._nodes = self._open(nodes_table)
        self._edges = self._open(edges_table)

    def _open(self, name: str) -> Any | None:
        try:
            return self._db.open_table(name)
        except Exception:
            return None

    @staticmethod
    def _serialize_props(props: Dict[str, Any]) -> str:
        return json.dumps(props or {}, ensure_ascii=False, default=str)

    @staticmethod
    def _deserialize_props(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _table_to_rows(table: Any) -> List[Dict[str, Any]]:
        if table is None:
            return []
        if hasattr(table, "to_arrow"):
            arrow = table.to_arrow()
            if hasattr(arrow, "to_pylist"):
                return list(arrow.to_pylist())
        if hasattr(table, "to_pandas"):
            return table.to_pandas().to_dict(orient="records")
        return []

    # ------------------------------------------------------------------
    # GraphStore ABC
    # ------------------------------------------------------------------

    def write(
        self,
        nodes: Sequence[Dict[str, Any]],
        relationships: Sequence[Dict[str, Any]],
        *,
        database: str = "neo4j",
        workspace_id: str = "default",
        source_id: str = "",
    ) -> Dict[str, Any]:
        summary = {"nodes_created": 0, "relationships_created": 0, "errors": []}

        node_rows: List[Dict[str, Any]] = []
        for node in nodes:
            label = str(node.get("label", "Entity"))
            props = dict(node.get("properties", {}))
            node_id = str(node.get("id", props.get("name", "")))
            if not node_id:
                summary["errors"].append(f"Node missing id: {node}")
                continue
            node_rows.append(
                {
                    "id": node_id,
                    "label": label,
                    "name": str(props.get("name", node_id)),
                    "properties": self._serialize_props(props),
                    "_source_id": source_id,
                    "_workspace_id": workspace_id,
                    "_ts": time.time(),
                }
            )

        edge_rows: List[Dict[str, Any]] = []
        for rel in relationships:
            rtype = str(rel.get("type", "RELATED_TO"))
            src = str(rel.get("source", ""))
            tgt = str(rel.get("target", ""))
            if not src or not tgt:
                summary["errors"].append(f"Edge missing source/target: {rel}")
                continue
            edge_rows.append(
                {
                    "edge_id": f"{src}|{rtype}|{tgt}",
                    "source": src,
                    "target": tgt,
                    "type": rtype,
                    "properties": self._serialize_props(rel.get("properties", {})),
                    "_source_id": source_id,
                    "_workspace_id": workspace_id,
                    "_ts": time.time(),
                }
            )

        if node_rows:
            self._nodes = self._upsert(self._nodes, self._nodes_table_name, node_rows, key="id")
            summary["nodes_created"] = len(node_rows)
        if edge_rows:
            self._edges = self._upsert(self._edges, self._edges_table_name, edge_rows, key="edge_id")
            summary["relationships_created"] = len(edge_rows)
        return summary

    def _upsert(self, table: Any, name: str, rows: List[Dict[str, Any]], *, key: str) -> Any:
        if table is None:
            return self._db.create_table(name, data=rows)
        keys = [r[key] for r in rows]
        if keys:
            quoted = ", ".join(f"'{k}'" for k in keys)
            try:
                table.delete(f"{key} IN ({quoted})")
            except Exception:
                pass
        table.add(rows)
        return table

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> List[Dict[str, Any]]:
        # Lance has no Cypher engine. We extract any quoted identifiers
        # from the Cypher string and do a name-based lookup so the
        # tutorial's auto-generated queries still produce something.
        # For real graph traversal use ``keyword_retrieve`` /
        # ``neighbors`` directly.
        merged_params = dict(params or {})
        candidates: List[str] = []
        for value in merged_params.values():
            if isinstance(value, str) and value:
                candidates.append(value)
        candidates.extend(_NAME_FROM_CYPHER_RE.findall(cypher))
        results: List[Dict[str, Any]] = []
        for name in candidates:
            results.extend(self.keyword_retrieve(name, limit=5))
        return results

    def ensure_constraints(
        self,
        ontology: Any,
        *,
        database: str = "neo4j",
        strict: bool = False,
        transactional: bool = False,
    ) -> Dict[str, Any]:
        # Lance is schemaless w.r.t. labels; nothing to enforce.
        return {"success": 0, "errors": []}

    def execute_write(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
        workspace_id: Optional[str] = None,
        enforce_workspace_filter: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "LanceGraphStore.execute_write: use write(nodes, relationships) "
            "instead, or wait for upstream lance-graph (issue #91)."
        )

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        node_rows = self._table_to_rows(self._nodes)
        edge_rows = self._table_to_rows(self._edges)
        labels = sorted({str(r.get("label", "")) for r in node_rows if r.get("label")})
        rel_types = sorted({str(r.get("type", "")) for r in edge_rows if r.get("type")})
        return {
            "labels": labels,
            "relationship_types": rel_types,
            "node_count": len(node_rows),
            "relationship_count": len(edge_rows),
        }

    def delete_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        nodes_deleted = 0
        relationships_deleted = 0
        safe = source_id.replace("'", "''")
        if self._nodes is not None:
            nodes_deleted = self._count_where(self._nodes, f"_source_id = '{safe}'")
            self._nodes.delete(f"_source_id = '{safe}'")
        if self._edges is not None:
            relationships_deleted = self._count_where(self._edges, f"_source_id = '{safe}'")
            self._edges.delete(f"_source_id = '{safe}'")
        return {
            "nodes_deleted": nodes_deleted,
            "relationships_deleted": relationships_deleted,
        }

    def count_by_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, int]:
        safe = source_id.replace("'", "''")
        return {
            "nodes": self._count_where(self._nodes, f"_source_id = '{safe}'"),
            "relationships": self._count_where(self._edges, f"_source_id = '{safe}'"),
        }

    def close(self) -> None:
        self._nodes = None
        self._edges = None

    @staticmethod
    def _count_where(table: Any, where: str) -> int:
        if table is None:
            return 0
        try:
            results = table.search().where(where)
            rows = LanceGraphStore._table_to_rows(results)
            return len(rows)
        except Exception:
            try:
                df = table.to_pandas()
                return int(df.query(where.replace("=", "==").replace("'", '"'), engine="python").shape[0])
            except Exception:
                return 0

    # ------------------------------------------------------------------
    # Tutorial-friendly retrievers
    # ------------------------------------------------------------------

    def keyword_retrieve(self, keyword: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        """Substring match on node ``name`` field."""
        rows = self._table_to_rows(self._nodes)
        kw = keyword.lower()
        hits: List[Dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name", ""))
            if kw in name.lower():
                hits.append(
                    {
                        "id": row.get("id"),
                        "label": row.get("label"),
                        "name": name,
                        "properties": self._deserialize_props(row.get("properties")),
                    }
                )
            if len(hits) >= limit:
                break
        return hits

    def neighbors(self, node_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Return one-hop neighbors of ``node_id`` with edge metadata."""
        edge_rows = self._table_to_rows(self._edges)
        node_rows = {r["id"]: r for r in self._table_to_rows(self._nodes)}
        out: List[Dict[str, Any]] = []
        for edge in edge_rows:
            other_id: Optional[str] = None
            direction = ""
            if edge.get("source") == node_id:
                other_id = edge.get("target")
                direction = "out"
            elif edge.get("target") == node_id:
                other_id = edge.get("source")
                direction = "in"
            if not other_id:
                continue
            other = node_rows.get(other_id)
            out.append(
                {
                    "neighbor_id": other_id,
                    "neighbor_label": other.get("label") if other else None,
                    "neighbor_name": other.get("name") if other else None,
                    "edge_type": edge.get("type"),
                    "direction": direction,
                    "edge_properties": self._deserialize_props(edge.get("properties")),
                }
            )
            if len(out) >= limit:
                break
        return out
