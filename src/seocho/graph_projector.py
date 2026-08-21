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
        ontology_context: Any = None,
    ) -> GraphProjectionResult:
        # Stamp the ontology-context provenance (_ontology_version /
        # _ontology_context_hash / _ontology_id ...) on every projected node and
        # relationship so drift detection (build_ontology_context_summary_query
        # + assess_ontology_context_mismatch) can tell which ontology version the
        # materialized data was written under. Without this the summary query
        # reads empty hashes and drift assessment is blind on projector-written
        # data (seocho-ia4.1). Best-effort: unchanged behavior when no context.
        ontology_props: Dict[str, str] = {}
        if ontology_context is not None:
            from .ontology_context import ontology_context_graph_properties

            ontology_props = {
                k: v for k, v in ontology_context_graph_properties(ontology_context).items() if v
            }

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
            for k, v in ontology_props.items():
                properties.setdefault(k, v)
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
            for k, v in ontology_props.items():
                properties.setdefault(k, v)
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
