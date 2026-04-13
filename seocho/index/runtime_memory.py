"""Canonical runtime-ingest memory graph helpers.

These helpers are deterministic and reusable across local/runtime ingestion
paths. They shape extracted graphs into the memory graph contract without
depending on extraction-side transport modules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple


def build_record_metadata(
    *,
    source_id: str,
    category: str,
    source_type: str,
    content_encoding: str,
    parser_metadata: Optional[Dict[str, Any]],
    user_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if isinstance(parser_metadata, dict):
        metadata.update(parser_metadata)
    if isinstance(user_metadata, dict):
        metadata.update(user_metadata)
    metadata.setdefault("source_id", source_id)
    metadata.setdefault("category", category)
    metadata.setdefault("source_type", source_type)
    metadata.setdefault("content_encoding", content_encoding)
    return metadata


def collect_entity_names(graph_data: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    for node in graph_data.get("nodes", []):
        label = str(node.get("label", "")).strip()
        if label == "Document":
            continue
        props = node.get("properties", {}) if isinstance(node, dict) else {}
        value = str(props.get("name", "")).strip().lower()
        if value:
            names.add(value)
    return names


def ensure_memory_graph(
    *,
    graph_data: Dict[str, Any],
    source_id: str,
    workspace_id: str,
    text: str,
    category: str,
    source_type: str,
    record_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    document_id = f"{source_id}_doc"
    preview = text[:280]
    metadata_json = json.dumps(record_metadata, ensure_ascii=False, sort_keys=True)
    timestamp = str(
        record_metadata.get("updated_at")
        or record_metadata.get("created_at")
        or _utc_now_iso()
    )
    node_map: Dict[str, Dict[str, Any]] = {}
    normalized_relationships: list[Dict[str, Any]] = []
    relationship_seen: Set[Tuple[str, str, str]] = set()

    for raw_node in graph_data.get("nodes", []):
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id", "")).strip()
        if not node_id:
            continue
        label = str(raw_node.get("label", "Entity")).strip() or "Entity"
        raw_props = raw_node.get("properties", {})
        properties = dict(raw_props) if isinstance(raw_props, dict) else {}
        properties.setdefault("source_id", source_id)
        properties.setdefault("memory_id", source_id)
        properties.setdefault("workspace_id", workspace_id)
        properties.setdefault("status", "active")
        properties.setdefault("category", category)
        properties.setdefault("source_type", source_type)
        properties.setdefault("updated_at", timestamp)
        if label == "Document":
            properties.setdefault("name", preview[:80] or source_id)
            properties.setdefault("title", preview[:120] or source_id)
            properties.setdefault("content", text)
            properties.setdefault("content_preview", preview)
            properties.setdefault("metadata_json", metadata_json)
            properties.setdefault("created_at", timestamp)
            copy_scope_properties(properties, record_metadata)
        else:
            properties.setdefault("content_preview", preview)
        node_map[node_id] = {"id": node_id, "label": label, "properties": properties}

    document_node = node_map.get(document_id, {"id": document_id, "label": "Document", "properties": {}})
    document_props = dict(document_node.get("properties", {}))
    document_props.update(
        {
            "name": document_props.get("name") or preview[:80] or source_id,
            "title": document_props.get("title") or preview[:120] or source_id,
            "content": text,
            "content_preview": preview,
            "source_id": source_id,
            "memory_id": source_id,
            "workspace_id": workspace_id,
            "source_type": source_type,
            "category": category,
            "status": document_props.get("status") or "active",
            "metadata_json": metadata_json,
            "updated_at": document_props.get("updated_at") or timestamp,
            "created_at": document_props.get("created_at") or timestamp,
        }
    )
    copy_scope_properties(document_props, record_metadata)
    node_map[document_id] = {"id": document_id, "label": "Document", "properties": document_props}

    for raw_rel in graph_data.get("relationships", []):
        if not isinstance(raw_rel, dict):
            continue
        source = str(raw_rel.get("source", "")).strip()
        target = str(raw_rel.get("target", "")).strip()
        rel_type = str(raw_rel.get("type", "RELATED_TO")).strip() or "RELATED_TO"
        if not source or not target:
            continue
        raw_props = raw_rel.get("properties", {})
        properties = dict(raw_props) if isinstance(raw_props, dict) else {}
        properties.setdefault("source_id", source_id)
        properties.setdefault("memory_id", source_id)
        properties.setdefault("workspace_id", workspace_id)
        key = (source, target, rel_type)
        if key in relationship_seen:
            continue
        relationship_seen.add(key)
        normalized_relationships.append(
            {
                "source": source,
                "target": target,
                "type": rel_type,
                "properties": properties,
            }
        )

    for node_id in list(node_map.keys()):
        if node_id == document_id:
            continue
        key = (document_id, node_id, "MENTIONS")
        if key in relationship_seen:
            continue
        relationship_seen.add(key)
        normalized_relationships.append(
            {
                "source": document_id,
                "target": node_id,
                "type": "MENTIONS",
                "properties": {
                    "source_id": source_id,
                    "memory_id": source_id,
                    "workspace_id": workspace_id,
                },
            }
        )

    semantic_payload = dict(graph_data.get("_semantic", {}))
    semantic_payload["record_context"] = record_metadata
    return {
        "nodes": list(node_map.values()),
        "relationships": normalized_relationships,
        "_semantic": semantic_payload,
    }


def copy_scope_properties(properties: Dict[str, Any], record_metadata: Dict[str, Any]) -> None:
    for key in ("user_id", "agent_id", "session_id", "created_at", "updated_at"):
        value = record_metadata.get(key)
        if value not in (None, ""):
            properties.setdefault(key, value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "build_record_metadata",
    "collect_entity_names",
    "ensure_memory_graph",
    "copy_scope_properties",
]
