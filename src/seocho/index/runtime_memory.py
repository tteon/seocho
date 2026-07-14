"""Canonical runtime-ingest memory graph helpers.

These helpers are deterministic and reusable across local/runtime ingestion
paths. They shape extracted graphs into the memory graph contract without
depending on extraction-side transport modules.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from seocho.index.metadata import (
    EntityField,
    MentionsField,
    RelatedToField,
    RunContext,
    provenance_stamp,
)


CONTENT_PREVIEW_CHAR_LIMIT = 1200
# Labels that legitimately need a document-level preview embedded on the node.
# All other (domain) labels — BusinessSegment, FinancialMetric, LegalEntity etc.
# — must NOT carry the document's evidence text on their properties, or the
# graph abstraction collapses to a vector chunk per node (T2.2).
_LABELS_KEEPING_DOC_PREVIEW = frozenset({"Document"})
_STRUCTURAL_LABELS = frozenset({"Document", "DocumentVersion", "Section", "Chunk"})
_STRUCTURAL_RELATIONSHIPS = frozenset(
    {"HAS_VERSION", "CURRENT_VERSION", "HAS_SECTION", "HAS_CHUNK", "NEXT", "PART_OF"}
)
ROOT_SECTION_PATH = "Document"


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
    chunk_records: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    document_id = str(record_metadata.get("document_id") or f"{source_id}_doc")
    preview = _content_preview(text)
    metadata_json = json.dumps(record_metadata, ensure_ascii=False, sort_keys=True)
    timestamp = str(
        record_metadata.get("updated_at")
        or record_metadata.get("created_at")
        or _utc_now_iso()
    )
    run_context = _run_context_from_metadata(
        source_id=source_id,
        record_metadata=record_metadata,
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
        properties.setdefault("created_at", timestamp)
        if label == "Document":
            properties.setdefault("name", preview[:80] or source_id)
            properties.setdefault("title", preview[:120] or source_id)
            properties.setdefault("content", text)
            properties.setdefault("content_preview", preview)
            properties.setdefault("metadata_json", metadata_json)
            properties.setdefault("created_at", timestamp)
            copy_scope_properties(properties, record_metadata)
        elif label in _LABELS_KEEPING_DOC_PREVIEW:
            # Reserved for future doc-like labels (none right now besides Document).
            properties.setdefault("content_preview", preview)
        elif label not in _STRUCTURAL_LABELS:
            _shape_entity_properties(
                properties,
                node_id=node_id,
                label=label,
                timestamp=timestamp,
            )
            copy_scope_properties(properties, record_metadata)
        # Domain nodes (BusinessSegment, FinancialMetric, etc.) intentionally
        # do NOT receive the document-wide content_preview — see T2.2.
        # Chunk nodes get their own short content_preview via _attach_chunk_layer.
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
        _shape_relationship_properties(
            properties,
            rel_type=rel_type,
            run_context=run_context,
        )
        key = (source, target, rel_type)
        if key in relationship_seen:
            continue
        relationship_seen.add(key)
        normalized_relationships.append(
            {
                "source": source,
                "target": target,
                "type": rel_type,
                "source_label": str(node_map.get(source, {}).get("label", "")),
                "target_label": str(node_map.get(target, {}).get("label", "")),
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
                "source_label": "Document",
                "target_label": str(node_map.get(node_id, {}).get("label", "")),
                "properties": _shape_relationship_properties(
                    {
                        "source_id": source_id,
                        "memory_id": source_id,
                        "workspace_id": workspace_id,
                    },
                    rel_type="MENTIONS",
                    run_context=run_context,
                ),
            }
        )

    layered_summary = _attach_chunk_layer(
        node_map=node_map,
        relationships=normalized_relationships,
        relationship_seen=relationship_seen,
        chunk_records=chunk_records,
        document_id=document_id,
        source_id=source_id,
        workspace_id=workspace_id,
        text=text,
        category=category,
        source_type=source_type,
        record_metadata=record_metadata,
        metadata_json=metadata_json,
        timestamp=timestamp,
        run_context=run_context,
    )

    _attach_relation_evidence_chunks(normalized_relationships)
    _refresh_entity_mention_counts(
        node_map,
        normalized_relationships,
        timestamp=timestamp,
    )

    semantic_payload = dict(graph_data.get("_semantic", {}))
    semantic_payload["record_context"] = record_metadata
    if layered_summary:
        semantic_payload["layered_graph_summary"] = layered_summary
    for relationship in normalized_relationships:
        source = str(relationship.get("source", ""))
        target = str(relationship.get("target", ""))
        relationship.setdefault(
            "source_label", str(node_map.get(source, {}).get("label", ""))
        )
        relationship.setdefault(
            "target_label", str(node_map.get(target, {}).get("label", ""))
        )
    return {
        "nodes": list(node_map.values()),
        "relationships": normalized_relationships,
        "_semantic": semantic_payload,
        "layered_graph_summary": layered_summary,
    }


def copy_scope_properties(properties: Dict[str, Any], record_metadata: Dict[str, Any]) -> None:
    for key in ("user_id", "agent_id", "session_id", "created_at", "updated_at"):
        value = record_metadata.get(key)
        if value not in (None, ""):
            properties.setdefault(key, value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_preview(text: str, limit: int = CONTENT_PREVIEW_CHAR_LIMIT) -> str:
    content = str(text or "").strip()
    if len(content) <= limit:
        return content
    return content[:limit].rsplit(" ", 1)[0].rstrip()


def _attach_chunk_layer(
    *,
    node_map: Dict[str, Dict[str, Any]],
    relationships: list[Dict[str, Any]],
    relationship_seen: Set[Tuple[str, str, str]],
    chunk_records: Optional[Iterable[Dict[str, Any]]],
    document_id: str,
    source_id: str,
    workspace_id: str,
    text: str,
    category: str,
    source_type: str,
    record_metadata: Dict[str, Any],
    metadata_json: str,
    timestamp: str,
    run_context: RunContext,
) -> Dict[str, Any]:
    records = [dict(item) for item in (chunk_records or []) if isinstance(item, dict)]
    if not records:
        return {}

    version_id = str(
        records[0].get("version_id")
        or record_metadata.get("version_id")
        or f"{source_id}_ver"
    )
    version_props: Dict[str, Any] = {
        "version_id": version_id,
        "document_id": document_id,
        "checksum": str(record_metadata.get("checksum") or _content_checksum(text)),
        "chunk_count": len(records),
        "memory_id": source_id,
        "source_id": source_id,
        "workspace_id": workspace_id,
        "source_type": source_type,
        "category": category,
        "status": "active",
        "metadata_json": metadata_json,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    copy_scope_properties(version_props, record_metadata)
    node_map[version_id] = {"id": version_id, "label": "DocumentVersion", "properties": version_props}

    _add_relationship(
        relationships,
        relationship_seen,
        source=document_id,
        target=version_id,
        rel_type="HAS_VERSION",
        properties={
            "memory_id": source_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
        },
    )
    _add_relationship(
        relationships,
        relationship_seen,
        source=document_id,
        target=version_id,
        rel_type="CURRENT_VERSION",
        properties={
            "memory_id": source_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
            "is_current": True,
        },
    )

    previous_chunk_id: Optional[str] = None
    chunk_ids: list[str] = []
    chunk_mentions = 0
    section_ids: list[str] = []
    section_paths: list[str] = []
    section_state: Dict[str, Dict[str, Any]] = {}

    for record in sorted(records, key=lambda item: int(item.get("ordinal", 0) or 0)):
        chunk_id = str(record.get("chunk_id") or f"{source_id}_chunk_{len(chunk_ids):04d}")
        chunk_text = str(record.get("text") or "")
        normalized_section_path = _normalize_section_path(record.get("section_path"))
        normalized_section_title = str(record.get("section_title") or "").strip()
        normalized_section_level = record.get("section_level")
        if normalized_section_level not in (None, ""):
            try:
                normalized_section_level = int(normalized_section_level)
            except (TypeError, ValueError):
                normalized_section_level = None
        chunk_props: Dict[str, Any] = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "version_id": version_id,
            "ordinal": int(record.get("ordinal", len(chunk_ids)) or 0),
            "text": chunk_text,
            "content_preview": _content_preview(chunk_text, limit=400),
            "char_start": record.get("char_start"),
            "char_end": record.get("char_end"),
            "token_count": int(record.get("token_count") or _rough_token_count(chunk_text)),
            "embedding_vector_id": str(record.get("embedding_vector_id") or chunk_id),
            "embedding_model": str(record.get("embedding_model") or ""),
            "embeddingText": str(record.get("embeddingText") or chunk_text),
            "section_path": normalized_section_path if normalized_section_path != ROOT_SECTION_PATH else "",
            "section_title": normalized_section_title,
            "section_level": normalized_section_level,
            "memory_id": source_id,
            "source_id": source_id,
            "workspace_id": workspace_id,
            "source_type": source_type,
            "category": category,
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        copy_scope_properties(chunk_props, record_metadata)
        node_map[chunk_id] = {"id": chunk_id, "label": "Chunk", "properties": chunk_props}
        chunk_ids.append(chunk_id)

        _add_relationship(
            relationships,
            relationship_seen,
            source=version_id,
            target=chunk_id,
            rel_type="HAS_CHUNK",
            properties={
                "memory_id": source_id,
                "source_id": source_id,
                "workspace_id": workspace_id,
                "ordinal": chunk_props["ordinal"],
            },
        )
        if previous_chunk_id:
            _add_relationship(
                relationships,
                relationship_seen,
                source=previous_chunk_id,
                target=chunk_id,
                rel_type="NEXT",
                properties={
                    "memory_id": source_id,
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                },
            )
        previous_chunk_id = chunk_id

        section_entries = _section_entries_for_record(
            normalized_section_path,
            normalized_section_title,
            normalized_section_level,
        )
        parent_section_id: Optional[str] = None
        leaf_section_id: Optional[str] = None
        for entry in section_entries:
            section_key = str(entry["path"])
            section_id = _section_node_id(version_id, section_key)
            leaf_section_id = section_id
            if section_key not in section_state:
                section_props: Dict[str, Any] = {
                    "section_id": section_id,
                    "document_id": document_id,
                    "version_id": version_id,
                    "section_path": section_key if section_key != ROOT_SECTION_PATH else "",
                    "title": str(entry["title"]),
                    "level": entry["level"],
                    "ordinal": len(section_state),
                    "memory_id": source_id,
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                    "source_type": source_type,
                    "category": category,
                    "status": "active",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                copy_scope_properties(section_props, record_metadata)
                node_map[section_id] = {"id": section_id, "label": "Section", "properties": section_props}
                section_state[section_key] = {"id": section_id, "level": entry["level"]}
                section_ids.append(section_id)
                section_paths.append(section_key if section_key != ROOT_SECTION_PATH else "")
            else:
                section_id = str(section_state[section_key]["id"])

            if parent_section_id is None:
                _add_relationship(
                    relationships,
                    relationship_seen,
                    source=version_id,
                    target=section_id,
                    rel_type="HAS_SECTION",
                    properties={
                        "memory_id": source_id,
                        "source_id": source_id,
                        "workspace_id": workspace_id,
                    },
                )
            else:
                _add_relationship(
                    relationships,
                    relationship_seen,
                    source=section_id,
                    target=parent_section_id,
                    rel_type="PART_OF",
                    properties={
                        "memory_id": source_id,
                        "source_id": source_id,
                        "workspace_id": workspace_id,
                    },
                )
            parent_section_id = section_id

        if leaf_section_id:
            _add_relationship(
                relationships,
                relationship_seen,
                source=leaf_section_id,
                target=chunk_id,
                rel_type="HAS_CHUNK",
                properties={
                    "memory_id": source_id,
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                    "ordinal": chunk_props["ordinal"],
                },
            )

        for entity_id in [
            str(entity_id).strip()
            for entity_id in (record.get("entity_ids") or [])
            if str(entity_id).strip()
        ]:
            if entity_id not in node_map:
                continue
            mention_props: Dict[str, Any] = {
                "memory_id": source_id,
                "source_id": source_id,
                "workspace_id": workspace_id,
            }
            for key in ("confidence", "char_start", "char_end"):
                value = record.get(key)
                if value not in (None, ""):
                    mention_props[key] = value
            _shape_relationship_properties(
                mention_props,
                rel_type="MENTIONS",
                run_context=run_context,
                evidence_span=chunk_text,
                char_start=record.get("char_start"),
                char_end=record.get("char_end"),
                role="chunk_evidence",
            )
            _add_relationship(
                relationships,
                relationship_seen,
                source=chunk_id,
                target=entity_id,
                rel_type="MENTIONS",
                properties=mention_props,
            )
            chunk_mentions += 1

    return {
        "document_id": document_id,
        "version_id": version_id,
        "section_count": len(section_ids),
        "section_ids": section_ids,
        "section_paths": section_paths,
        "chunk_count": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "chunk_mentions": chunk_mentions,
    }


def _add_relationship(
    relationships: list[Dict[str, Any]],
    relationship_seen: Set[Tuple[str, str, str]],
    *,
    source: str,
    target: str,
    rel_type: str,
    properties: Dict[str, Any],
) -> None:
    key = (source, target, rel_type)
    if key in relationship_seen:
        return
    relationship_seen.add(key)
    relationships.append(
        {
            "source": source,
            "target": target,
            "type": rel_type,
            "properties": properties,
        }
    )


def _run_context_from_metadata(*, source_id: str, record_metadata: Dict[str, Any]) -> RunContext:
    extraction_run_id = str(
        record_metadata.get("extraction_run_id")
        or record_metadata.get("run_id")
        or _short_metadata_hash(source_id, record_metadata)
    )
    return RunContext(
        extraction_run_id=extraction_run_id,
        extracted_by=str(
            record_metadata.get("extracted_by")
            or record_metadata.get("model")
            or "seocho.index"
        ),
        prompt_version=str(record_metadata.get("prompt_version") or "runtime-memory-v1"),
        ontology_slice_hash=str(record_metadata.get("ontology_slice_hash") or ""),
        workspace_id=str(record_metadata.get("workspace_id") or "") or None,
    )


def _short_metadata_hash(source_id: str, record_metadata: Dict[str, Any]) -> str:
    version_id = str(record_metadata.get("version_id") or "")
    checksum = str(record_metadata.get("checksum") or "")
    payload = "|".join([source_id, version_id, checksum])
    return f"run-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _shape_entity_properties(
    properties: Dict[str, Any],
    *,
    node_id: str,
    label: str,
    timestamp: str,
) -> None:
    properties.setdefault(EntityField.ID, node_id)
    properties.setdefault(EntityField.CLASS, label)
    properties.setdefault(EntityField.NAME, properties.get("name") or node_id)
    properties.setdefault(EntityField.FIRST_SEEN_AT, timestamp)
    properties.setdefault(EntityField.LAST_SEEN_AT, timestamp)
    properties.setdefault(EntityField.MENTION_COUNT, 0)


def _shape_relationship_properties(
    properties: Dict[str, Any],
    *,
    rel_type: str,
    run_context: RunContext,
    evidence_span: Optional[str] = None,
    char_start: Any = None,
    char_end: Any = None,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    if rel_type in _STRUCTURAL_RELATIONSHIPS:
        return properties

    stamp = provenance_stamp(
        run_context,
        confidence=_optional_float(properties.get("confidence")),
        evidence_span=evidence_span or properties.get(MentionsField.EVIDENCE_SPAN),
        char_start=_optional_int(char_start if char_start not in (None, "") else properties.get("char_start")),
        char_end=_optional_int(char_end if char_end not in (None, "") else properties.get("char_end")),
        role=role or properties.get(MentionsField.ROLE),
    )
    for key, value in stamp.items():
        properties.setdefault(key, value)
    if rel_type != "MENTIONS":
        properties.setdefault(RelatedToField.WEIGHT, properties.get("confidence", 1.0))
    return properties


def _attach_relation_evidence_chunks(relationships: list[Dict[str, Any]]) -> None:
    mentions_by_entity: Dict[str, list[str]] = {}
    for rel in relationships:
        if rel.get("type") != "MENTIONS":
            continue
        source = str(rel.get("source") or "")
        target = str(rel.get("target") or "")
        if "_chunk_" not in source or not target:
            continue
        mentions_by_entity.setdefault(target, []).append(source)

    for rel in relationships:
        rel_type = str(rel.get("type") or "")
        if rel_type in _STRUCTURAL_RELATIONSHIPS or rel_type == "MENTIONS":
            continue
        source = str(rel.get("source") or "")
        target = str(rel.get("target") or "")
        source_chunks = set(mentions_by_entity.get(source, []))
        target_chunks = set(mentions_by_entity.get(target, []))
        evidence_chunks = sorted(source_chunks & target_chunks or source_chunks | target_chunks)
        if not evidence_chunks:
            continue
        props = rel.setdefault("properties", {})
        props.setdefault(RelatedToField.EVIDENCE_CHUNKS, evidence_chunks)
        props.setdefault("evidence_chunk_count", len(evidence_chunks))


def _refresh_entity_mention_counts(
    node_map: Dict[str, Dict[str, Any]],
    relationships: list[Dict[str, Any]],
    *,
    timestamp: str,
) -> None:
    chunk_counts: Dict[str, int] = {}
    fallback_counts: Dict[str, int] = {}
    for rel in relationships:
        if rel.get("type") != "MENTIONS":
            continue
        source = str(rel.get("source") or "")
        target = str(rel.get("target") or "")
        if not target:
            continue
        if "_chunk_" in source:
            chunk_counts[target] = chunk_counts.get(target, 0) + 1
        else:
            fallback_counts[target] = fallback_counts.get(target, 0) + 1

    for node_id, node in node_map.items():
        label = str(node.get("label") or "")
        if label in _STRUCTURAL_LABELS:
            continue
        properties = node.setdefault("properties", {})
        counts = chunk_counts if node_id in chunk_counts else fallback_counts
        if node_id in counts:
            properties[EntityField.MENTION_COUNT] = counts[node_id]
            properties[EntityField.LAST_SEEN_AT] = timestamp


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _content_checksum(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _rough_token_count(text: str) -> int:
    content = str(text or "").strip()
    if not content:
        return 0
    return len(content.split())


def _normalize_section_path(value: Any) -> str:
    section_path = str(value or "").strip()
    return section_path or ROOT_SECTION_PATH


def _section_entries_for_record(
    section_path: str,
    section_title: str,
    section_level: Any,
) -> list[Dict[str, Any]]:
    path = _normalize_section_path(section_path)
    parts = [part.strip() for part in path.split("/") if part.strip()]
    if not parts:
        parts = [ROOT_SECTION_PATH]
    target_level: Optional[int]
    try:
        target_level = int(section_level) if section_level not in (None, "") else None
    except (TypeError, ValueError):
        target_level = None
    entries: list[Dict[str, Any]] = []
    for index in range(len(parts)):
        prefix = " / ".join(parts[: index + 1])
        inferred_level = index + 1
        entries.append(
            {
                "path": prefix,
                "title": parts[index],
                "level": target_level if index == len(parts) - 1 and target_level is not None else inferred_level,
            }
        )
    if entries and section_title and section_title.strip():
        entries[-1]["title"] = section_title.strip()
    return entries


def _section_node_id(version_id: str, section_path: str) -> str:
    normalized = _normalize_section_path(section_path)
    if normalized == ROOT_SECTION_PATH:
        return f"{version_id}_section_root"
    suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{version_id}_section_{suffix}"


__all__ = [
    "build_record_metadata",
    "collect_entity_names",
    "ensure_memory_graph",
    "copy_scope_properties",
]
