from __future__ import annotations

from typing import Any, Dict, List

from .qualification import GraphProjectionResult, GraphProjectionSnapshot


class GraphProjector:
    """Project canonical qualification snapshots into a graph store."""

    def __init__(self, *, graph_store: Any, workspace_id: str) -> None:
        self.graph_store = graph_store
        self.workspace_id = workspace_id

    def project(
        self,
        snapshot: GraphProjectionSnapshot,
        *,
        database: str,
    ) -> GraphProjectionResult:
        nodes: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        for entity in snapshot.entities:
            properties = dict(entity.properties)
            properties.setdefault("entity_id", entity.entity_id)
            properties.setdefault("canonical_name", entity.canonical_name)
            properties.setdefault("support_count", entity.support_count)
            properties.setdefault("workspace_id", snapshot.workspace_id)
            properties.setdefault("graph_id", snapshot.graph_id)
            properties.setdefault("snapshot_id", snapshot.snapshot_id)
            nodes.append(
                {
                    "id": entity.entity_id,
                    "label": entity.entity_type or "Entity",
                    "properties": properties,
                }
            )

        for relation in snapshot.relationships:
            properties = dict(relation.properties)
            properties.setdefault("relation_id", relation.relation_id)
            properties.setdefault("support_count", relation.support_count)
            properties.setdefault("workspace_id", snapshot.workspace_id)
            properties.setdefault("graph_id", snapshot.graph_id)
            properties.setdefault("snapshot_id", snapshot.snapshot_id)
            relationships.append(
                {
                    "source": relation.source_entity_id,
                    "target": relation.target_entity_id,
                    "type": relation.rel_type,
                    "properties": properties,
                }
            )

        summary = self.graph_store.write(
            nodes,
            relationships,
            database=database,
            workspace_id=self.workspace_id,
            source_id=snapshot.snapshot_id,
        )
        return GraphProjectionResult(
            snapshot_id=snapshot.snapshot_id,
            workspace_id=snapshot.workspace_id,
            graph_id=snapshot.graph_id,
            database=database,
            store_backend=type(self.graph_store).__name__,
            nodes_written=int(summary.get("nodes_created", len(nodes)) or 0),
            relationships_written=int(summary.get("relationships_created", len(relationships)) or 0),
            summary=dict(summary or {}),
        )
