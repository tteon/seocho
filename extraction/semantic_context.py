from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence


def build_dynamic_prompt_context(
    *,
    category: str,
    source_type: str,
    ontology_candidate: Optional[Dict[str, Any]] = None,
    shacl_candidate: Optional[Dict[str, Any]] = None,
    vocabulary_candidate: Optional[Dict[str, Any]] = None,
    approved_artifacts: Optional[Dict[str, Any]] = None,
    record_metadata: Optional[Dict[str, Any]] = None,
    entity_graph: Optional[Dict[str, Any]] = None,
    graph_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    approved = approved_artifacts or {}
    developer_context = _normalize_semantic_prompt_context(record_metadata)

    active_ontology = _merge_ontology_candidates(
        [
            _pick_candidate(
                approved.get("ontology_candidate"),
                ontology_candidate,
                default={"ontology_name": "", "classes": [], "relationships": []},
            ),
            developer_context.get("ontology_candidate"),
        ]
    )
    active_shacl = _merge_shacl_candidates(
        [
            _pick_candidate(
                approved.get("shacl_candidate"),
                shacl_candidate,
                default={"shapes": []},
            ),
            developer_context.get("shacl_candidate"),
        ]
    )
    active_vocabulary = _merge_vocabulary_candidates(
        [
            _pick_candidate(
                approved.get("vocabulary_candidate"),
                vocabulary_candidate,
                default={"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
            ),
            developer_context.get("vocabulary_candidate"),
        ]
    )
    ontology_name = str(
        active_ontology.get("ontology_name")
        or (graph_metadata or {}).get("ontology_id")
        or "runtime_candidate"
    ).strip()

    return {
        "category": category,
        "source_type": source_type,
        "ontology_name": ontology_name,
        "entity_types": _render_entity_types(active_ontology),
        "relationship_types": _render_relationship_types(active_ontology),
        "shacl_constraints": _render_shacl_constraints(active_shacl),
        "vocabulary_terms": _render_vocabulary_terms(active_vocabulary),
        "record_metadata_json": _json_dump(record_metadata or {}),
        "graph_context": _render_graph_context(graph_metadata),
        "entity_guidance": _merge_text_blocks(
            _render_entity_guidance(entity_graph),
            _render_known_entities(developer_context.get("known_entities")),
        ),
        "developer_instructions": _render_developer_instructions(developer_context),
        "ontology_context_notes": _render_context_notes(
            active_ontology,
            active_vocabulary,
            source_type,
            graph_metadata=graph_metadata,
        ),
    }


def _pick_candidate(primary: Any, fallback: Any, default: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(primary, dict) and primary:
        return primary
    if isinstance(fallback, dict) and fallback:
        return fallback
    return default


def _normalize_semantic_prompt_context(record_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(record_metadata, dict):
        return {}
    raw = record_metadata.get("semantic_prompt_context")
    if not isinstance(raw, dict):
        return {}
    return {
        "instructions": _clean_list(raw.get("instructions")) or _clean_list(raw.get("notes")),
        "known_entities": _normalize_known_entities(raw.get("known_entities")),
        "ontology_candidate": raw.get("ontology_candidate") if isinstance(raw.get("ontology_candidate"), dict) else {},
        "shacl_candidate": raw.get("shacl_candidate") if isinstance(raw.get("shacl_candidate"), dict) else {},
        "vocabulary_candidate": raw.get("vocabulary_candidate") if isinstance(raw.get("vocabulary_candidate"), dict) else {},
    }


def _merge_ontology_candidates(candidates: Sequence[Any]) -> Dict[str, Any]:
    merged_classes: Dict[str, Dict[str, Any]] = {}
    merged_relationships: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    ontology_name = ""

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("ontology_name", "")).strip()
        if name:
            ontology_name = name

        for cls in candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            class_name = str(cls.get("name", "")).strip()
            if not class_name:
                continue
            existing = merged_classes.setdefault(
                class_name,
                {
                    "name": class_name,
                    "description": "",
                    "aliases": [],
                    "broader": [],
                    "related": [],
                    "properties": [],
                },
            )
            description = str(cls.get("description", "")).strip()
            if description:
                existing["description"] = description
            existing["aliases"] = _merge_string_lists(existing.get("aliases", []), cls.get("aliases", []))
            existing["broader"] = _merge_string_lists(existing.get("broader", []), cls.get("broader", []))
            existing["related"] = _merge_string_lists(existing.get("related", []), cls.get("related", []))

            prop_map = {
                str(prop.get("name", "")).strip(): prop
                for prop in existing["properties"]
                if isinstance(prop, dict) and str(prop.get("name", "")).strip()
            }
            for prop in cls.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                prop_name = str(prop.get("name", "")).strip()
                if not prop_name:
                    continue
                current = prop_map.get(prop_name)
                if current is None:
                    current = {
                        "name": prop_name,
                        "datatype": str(prop.get("datatype", "string")).strip() or "string",
                        "description": str(prop.get("description", "")).strip(),
                        "aliases": _clean_list(prop.get("aliases")),
                    }
                    existing["properties"].append(current)
                    prop_map[prop_name] = current
                    continue
                datatype = str(prop.get("datatype", "")).strip()
                description = str(prop.get("description", "")).strip()
                if datatype:
                    current["datatype"] = datatype
                if description:
                    current["description"] = description
                current["aliases"] = _merge_string_lists(current.get("aliases", []), prop.get("aliases", []))

        for rel in candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            rel_type = str(rel.get("type", "")).strip()
            source = str(rel.get("source", "")).strip()
            target = str(rel.get("target", "")).strip()
            if not rel_type:
                continue
            key = (rel_type, source, target)
            existing = merged_relationships.setdefault(
                key,
                {
                    "type": rel_type,
                    "source": source,
                    "target": target,
                    "description": "",
                    "aliases": [],
                    "related": [],
                },
            )
            description = str(rel.get("description", "")).strip()
            if description:
                existing["description"] = description
            existing["aliases"] = _merge_string_lists(existing.get("aliases", []), rel.get("aliases", []))
            existing["related"] = _merge_string_lists(existing.get("related", []), rel.get("related", []))

    return {
        "ontology_name": ontology_name,
        "classes": list(merged_classes.values()),
        "relationships": list(merged_relationships.values()),
    }


def _merge_shacl_candidates(candidates: Sequence[Any]) -> Dict[str, Any]:
    shape_map: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for shape in candidate.get("shapes", []):
            if not isinstance(shape, dict):
                continue
            target_class = str(shape.get("target_class", "")).strip()
            if not target_class:
                continue
            existing = shape_map.setdefault(target_class, {"target_class": target_class, "properties": []})
            seen = {
                (
                    prop.get("path"),
                    prop.get("constraint"),
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                for prop in existing["properties"]
                if isinstance(prop, dict)
            }
            for prop in shape.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("path", "")).strip()
                constraint = str(prop.get("constraint", "")).strip()
                if not path or not constraint:
                    continue
                key = (
                    path,
                    constraint,
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                if key in seen:
                    continue
                seen.add(key)
                existing["properties"].append(
                    {
                        "path": path,
                        "constraint": constraint,
                        "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                    }
                )
    return {"shapes": list(shape_map.values())}


def _merge_vocabulary_candidates(candidates: Sequence[Any]) -> Dict[str, Any]:
    term_map: Dict[str, Dict[str, Any]] = {}
    schema_version = "vocabulary.v2"
    profile = "skos"

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        schema_version = str(candidate.get("schema_version", schema_version)).strip() or schema_version
        profile = str(candidate.get("profile", profile)).strip() or profile
        for term in candidate.get("terms", []):
            if not isinstance(term, dict):
                continue
            pref_label = str(
                term.get("pref_label")
                or term.get("canonical")
                or term.get("name")
                or ""
            ).strip()
            if not pref_label:
                continue
            key = pref_label.lower()
            existing = term_map.setdefault(
                key,
                {
                    "pref_label": pref_label,
                    "alt_labels": [],
                    "hidden_labels": [],
                    "broader": [],
                    "related": [],
                    "sources": [],
                    "definition": "",
                    "examples": [],
                },
            )
            definition = str(term.get("definition", "")).strip()
            if definition:
                existing["definition"] = definition
            existing["alt_labels"] = _merge_string_lists(
                existing.get("alt_labels", []),
                term.get("alt_labels", []) or term.get("aliases", []),
            )
            existing["hidden_labels"] = _merge_string_lists(existing.get("hidden_labels", []), term.get("hidden_labels", []))
            existing["broader"] = _merge_string_lists(existing.get("broader", []), term.get("broader", []))
            existing["related"] = _merge_string_lists(existing.get("related", []), term.get("related", []))
            existing["sources"] = _merge_string_lists(existing.get("sources", []), term.get("sources", []))
            existing["examples"] = _merge_string_lists(existing.get("examples", []), term.get("examples", []))

    return {"schema_version": schema_version, "profile": profile, "terms": list(term_map.values())}


def _render_entity_types(ontology_candidate: Dict[str, Any]) -> str:
    lines: List[str] = []
    for cls in ontology_candidate.get("classes", []):
        if not isinstance(cls, dict):
            continue
        name = str(cls.get("name", "")).strip()
        if not name:
            continue
        description = str(cls.get("description", "")).strip()
        aliases = _clean_list(cls.get("aliases"))
        broader = _clean_list(cls.get("broader"))
        related = _clean_list(cls.get("related"))
        props = []
        for prop in cls.get("properties", []):
            if not isinstance(prop, dict):
                continue
            prop_name = str(prop.get("name", "")).strip()
            if not prop_name:
                continue
            datatype = str(prop.get("datatype", "string")).strip() or "string"
            props.append(f"{prop_name}:{datatype}")
        fragments = [name]
        if description:
            fragments.append(description)
        if props:
            fragments.append(f"properties={', '.join(props)}")
        if aliases:
            fragments.append(f"aliases={', '.join(aliases)}")
        if broader:
            fragments.append(f"broader={', '.join(broader)}")
        if related:
            fragments.append(f"related={', '.join(related)}")
        lines.append("- " + " | ".join(fragments))
    return "\n".join(lines)


def _render_relationship_types(ontology_candidate: Dict[str, Any]) -> str:
    lines: List[str] = []
    for rel in ontology_candidate.get("relationships", []):
        if not isinstance(rel, dict):
            continue
        rel_type = str(rel.get("type", "")).strip()
        if not rel_type:
            continue
        source = str(rel.get("source", "")).strip() or "Entity"
        target = str(rel.get("target", "")).strip() or "Entity"
        description = str(rel.get("description", "")).strip()
        aliases = _clean_list(rel.get("aliases"))
        related = _clean_list(rel.get("related"))
        fragments = [f"{rel_type}: {source} -> {target}"]
        if description:
            fragments.append(description)
        if aliases:
            fragments.append(f"aliases={', '.join(aliases)}")
        if related:
            fragments.append(f"related={', '.join(related)}")
        lines.append("- " + " | ".join(fragments))
    return "\n".join(lines)


def _render_shacl_constraints(shacl_candidate: Dict[str, Any]) -> str:
    lines: List[str] = []
    for shape in shacl_candidate.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        target_class = str(shape.get("target_class", "")).strip()
        if not target_class:
            continue
        for prop in shape.get("properties", []):
            if not isinstance(prop, dict):
                continue
            path = str(prop.get("path", "")).strip()
            constraint = str(prop.get("constraint", "")).strip()
            params = prop.get("params", {}) if isinstance(prop.get("params"), dict) else {}
            if not path or not constraint:
                continue
            fragment = f"- {target_class}.{path}: {constraint}"
            if params:
                fragment += f" {json.dumps(params, sort_keys=True)}"
            lines.append(fragment)
    return "\n".join(lines)


def _render_vocabulary_terms(vocabulary_candidate: Dict[str, Any]) -> str:
    lines: List[str] = []
    for term in vocabulary_candidate.get("terms", []):
        if not isinstance(term, dict):
            continue
        pref_label = str(
            term.get("pref_label")
            or term.get("canonical")
            or term.get("name")
            or ""
        ).strip()
        if not pref_label:
            continue
        alt_labels = _clean_list(term.get("alt_labels"))
        if not alt_labels:
            alt_labels = _clean_list(term.get("aliases"))
        hidden_labels = _clean_list(term.get("hidden_labels"))
        broader = _clean_list(term.get("broader"))
        related = _clean_list(term.get("related"))
        sources = _clean_list(term.get("sources"))
        fragments = [f"prefLabel={pref_label}"]
        if alt_labels:
            fragments.append(f"altLabels={', '.join(alt_labels)}")
        if hidden_labels:
            fragments.append(f"hiddenLabels={', '.join(hidden_labels)}")
        if broader:
            fragments.append(f"broader={', '.join(broader)}")
        if related:
            fragments.append(f"related={', '.join(related)}")
        if sources:
            fragments.append(f"sources={', '.join(sources)}")
        lines.append("- " + " | ".join(fragments))
    return "\n".join(lines)


def _render_entity_guidance(entity_graph: Optional[Dict[str, Any]]) -> str:
    if not isinstance(entity_graph, dict):
        return ""
    lines: List[str] = []
    for node in entity_graph.get("nodes", [])[:12]:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", "Entity")).strip() or "Entity"
        props = node.get("properties", {}) if isinstance(node.get("properties"), dict) else {}
        display_name = str(
            props.get("name")
            or props.get("title")
            or props.get("id")
            or node.get("id", "")
        ).strip()
        if display_name:
            lines.append(f"- {label}: {display_name}")
    return "\n".join(lines)


def _render_known_entities(values: Any) -> str:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ""
    lines: List[str] = []
    for item in values[:12]:
        if isinstance(item, dict):
            label = str(item.get("label", "Entity")).strip() or "Entity"
            name = str(item.get("name") or item.get("value") or item.get("id") or "").strip()
            if name:
                lines.append(f"- {label}: {name}")
            continue
        text = str(item).strip()
        if text:
            lines.append(f"- Entity: {text}")
    return "\n".join(lines)


def _render_graph_context(graph_metadata: Optional[Dict[str, Any]]) -> str:
    if not isinstance(graph_metadata, dict) or not graph_metadata:
        return ""
    fragments: List[str] = []
    graph_id = str(graph_metadata.get("graph_id", "")).strip()
    database = str(graph_metadata.get("database", "")).strip()
    ontology_id = str(graph_metadata.get("ontology_id", "")).strip()
    vocabulary_profile = str(graph_metadata.get("vocabulary_profile", "")).strip()
    description = str(graph_metadata.get("description", "")).strip()
    workspace_scope = str(graph_metadata.get("workspace_scope", "")).strip()
    if graph_id:
        fragments.append(f"- Graph ID: {graph_id}")
    if database:
        fragments.append(f"- Database: {database}")
    if ontology_id:
        fragments.append(f"- Ontology ID: {ontology_id}")
    if vocabulary_profile:
        fragments.append(f"- Vocabulary Profile: {vocabulary_profile}")
    if workspace_scope:
        fragments.append(f"- Workspace Scope: {workspace_scope}")
    if description:
        fragments.append(f"- Graph Description: {description}")
    return "\n".join(fragments)


def _render_developer_instructions(developer_context: Dict[str, Any]) -> str:
    instructions = _clean_list(developer_context.get("instructions"))
    if not instructions:
        return ""
    return "\n".join(f"- {item}" for item in instructions)


def _render_context_notes(
    ontology_candidate: Dict[str, Any],
    vocabulary_candidate: Dict[str, Any],
    source_type: str,
    *,
    graph_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    note_lines = [
        f"- Source type: {source_type}",
        "- Prefer canonical ontology and vocabulary labels when aliases appear.",
        "- Preserve provenance-friendly labels and properties for downstream graph reasoning.",
    ]
    if isinstance(graph_metadata, dict):
        graph_desc = str(graph_metadata.get("description", "")).strip()
        ontology_id = str(graph_metadata.get("ontology_id", "")).strip()
        vocabulary_profile = str(graph_metadata.get("vocabulary_profile", "")).strip()
        if graph_desc:
            note_lines.append(f"- Graph context: {graph_desc}")
        if ontology_id:
            note_lines.append(f"- Align labels to ontology_id={ontology_id} when possible.")
        if vocabulary_profile:
            note_lines.append(f"- Use vocabulary profile {vocabulary_profile} for aliases and canonical terms.")
    if ontology_candidate.get("classes"):
        note_lines.append("- Favor extracted entities that match ontology classes over generic Entity labels.")
    if vocabulary_candidate.get("terms"):
        note_lines.append("- Use vocabulary terms as in-context hints for alias normalization and SKOS-compatible labels.")
    return "\n".join(note_lines)


def _clean_list(values: Any) -> List[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    cleaned: List[str] = []
    seen = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _normalize_known_entities(values: Any) -> List[Any]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    normalized: List[Any] = []
    for value in values:
        if isinstance(value, dict):
            item: Dict[str, Any] = {}
            for key in ("label", "name", "value", "id"):
                if key in value and value[key] not in (None, ""):
                    item[key] = str(value[key]).strip()
            if item:
                normalized.append(item)
            continue
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _merge_string_lists(left: Any, right: Any) -> List[str]:
    return _clean_list([*_clean_list(left), *_clean_list(right)])


def _merge_text_blocks(*parts: str) -> str:
    cleaned = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n".join(cleaned)


def _json_dump(payload: Dict[str, Any]) -> str:
    if not payload:
        return "{}"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
