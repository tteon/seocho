"""Canonical runtime-ingest semantic artifact helpers.

These helpers merge runtime ontology/SHACL candidates, build lightweight
vocabulary artifacts, and summarize deterministic semantic metadata without
depending on extraction-side transport modules.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple


def merge_ontology_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_classes: Dict[str, Dict[str, Any]] = {}
    merged_relationships: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    ontology_names: List[str] = []

    for item in candidates:
        name = str(item.get("ontology_name", "")).strip()
        if name:
            ontology_names.append(name)
        for cls in item.get("classes", []):
            cls_name = str(cls.get("name", "")).strip()
            if not cls_name:
                continue
            existing = merged_classes.setdefault(
                cls_name,
                {
                    "name": cls_name,
                    "description": str(cls.get("description", "")).strip(),
                    "aliases": [],
                    "broader": [],
                    "related": [],
                    "properties": [],
                },
            )
            if not existing.get("description"):
                existing["description"] = str(cls.get("description", "")).strip()
            existing["aliases"] = merge_string_lists(existing.get("aliases", []), cls.get("aliases", []))
            existing["broader"] = merge_string_lists(existing.get("broader", []), cls.get("broader", []))
            existing["related"] = merge_string_lists(existing.get("related", []), cls.get("related", []))
            existing_props = {
                str(prop.get("name", "")).strip(): prop
                for prop in existing["properties"]
                if isinstance(prop, dict) and str(prop.get("name", "")).strip()
            }
            for prop in cls.get("properties", []):
                prop_name = str(prop.get("name", "")).strip()
                if not prop_name:
                    continue
                existing_prop = existing_props.get(prop_name)
                if existing_prop is None:
                    existing_prop = {
                        "name": prop_name,
                        "datatype": str(prop.get("datatype", "string")).strip() or "string",
                        "description": str(prop.get("description", "")).strip(),
                        "aliases": clean_string_list(prop.get("aliases", [])),
                    }
                    existing["properties"].append(existing_prop)
                    existing_props[prop_name] = existing_prop
                    continue
                if not existing_prop.get("description"):
                    existing_prop["description"] = str(prop.get("description", "")).strip()
                if not existing_prop.get("datatype"):
                    existing_prop["datatype"] = str(prop.get("datatype", "string")).strip() or "string"
                existing_prop["aliases"] = merge_string_lists(
                    existing_prop.get("aliases", []),
                    prop.get("aliases", []),
                )

        for rel in item.get("relationships", []):
            rel_type = str(rel.get("type", "")).strip()
            source = str(rel.get("source", "")).strip()
            target = str(rel.get("target", "")).strip()
            if not rel_type:
                continue
            rel_key = (rel_type, source, target)
            existing_rel = merged_relationships.setdefault(
                rel_key,
                {
                    "type": rel_type,
                    "source": source,
                    "target": target,
                    "description": "",
                    "aliases": [],
                    "related": [],
                },
            )
            if not existing_rel.get("description"):
                existing_rel["description"] = str(rel.get("description", "")).strip()
            existing_rel["aliases"] = merge_string_lists(existing_rel.get("aliases", []), rel.get("aliases", []))
            existing_rel["related"] = merge_string_lists(existing_rel.get("related", []), rel.get("related", []))

    ontology_name = ontology_names[0] if ontology_names else "runtime_candidate_merged"
    return {
        "ontology_name": ontology_name,
        "classes": list(merged_classes.values()),
        "relationships": list(merged_relationships.values()),
    }


def merge_shacl_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    shape_map: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        for shape in item.get("shapes", []):
            target = str(shape.get("target_class", "")).strip()
            if not target:
                continue
            existing = shape_map.setdefault(target, {"target_class": target, "properties": []})
            seen_keys = {
                (
                    prop.get("path"),
                    prop.get("constraint"),
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                for prop in existing["properties"]
            }
            for prop in shape.get("properties", []):
                key = (
                    prop.get("path"),
                    prop.get("constraint"),
                    json.dumps(prop.get("params", {}), sort_keys=True),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                existing["properties"].append(
                    {
                        "path": str(prop.get("path", "")).strip(),
                        "constraint": str(prop.get("constraint", "")).strip(),
                        "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                    }
                )
    return {"shapes": list(shape_map.values())}


def build_vocabulary_candidate(
    ontology_candidate: Dict[str, Any],
    shacl_candidate: Dict[str, Any],
    prepared_graphs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    term_map: Dict[str, Dict[str, Any]] = {}

    def upsert_term(
        canonical: str,
        *,
        source: str,
        alt_labels: Optional[List[Any]] = None,
        hidden_labels: Optional[List[Any]] = None,
        broader: Optional[List[Any]] = None,
        related: Optional[List[Any]] = None,
        definition: str = "",
        examples: Optional[List[Any]] = None,
    ) -> None:
        canonical_text = str(canonical).strip()
        if not canonical_text:
            return
        key = canonical_text.lower()
        existing = term_map.setdefault(
            key,
            {
                "pref_label": canonical_text,
                "alt_labels": set(),
                "hidden_labels": set(),
                "broader": set(),
                "related": set(),
                "definition": "",
                "sources": set(),
                "examples": set(),
            },
        )
        existing["sources"].add(source)
        if definition and not existing["definition"]:
            existing["definition"] = definition
        for alias in alt_labels or []:
            alias_text = str(alias).strip()
            if alias_text and alias_text.lower() != key:
                existing["alt_labels"].add(alias_text)
        for alias in hidden_labels or []:
            alias_text = str(alias).strip()
            if alias_text and alias_text.lower() != key:
                existing["hidden_labels"].add(alias_text)
        for value in broader or []:
            broader_text = str(value).strip()
            if broader_text:
                existing["broader"].add(broader_text)
        for value in related or []:
            related_text = str(value).strip()
            if related_text:
                existing["related"].add(related_text)
        for value in examples or []:
            example_text = str(value).strip()
            if example_text:
                existing["examples"].add(example_text)

    for cls in ontology_candidate.get("classes", []):
        if not isinstance(cls, dict):
            continue
        class_name = str(cls.get("name", "")).strip()
        upsert_term(
            class_name,
            source="ontology.class",
            alt_labels=clean_string_list(cls.get("aliases", [])),
            broader=clean_string_list(cls.get("broader", [])),
            related=clean_string_list(cls.get("related", [])),
            definition=str(cls.get("description", "")).strip(),
        )
        for prop in cls.get("properties", []):
            if not isinstance(prop, dict):
                continue
            prop_name = str(prop.get("name", "")).strip()
            if not prop_name:
                continue
            qualified_name = f"{class_name}.{prop_name}" if class_name else prop_name
            upsert_term(
                qualified_name,
                source="ontology.property",
                alt_labels=clean_string_list(prop.get("aliases", [])),
                definition=str(prop.get("description", "")).strip(),
            )

    for rel in ontology_candidate.get("relationships", []):
        if not isinstance(rel, dict):
            continue
        upsert_term(
            str(rel.get("type", "")).strip(),
            source="ontology.relationship",
            alt_labels=clean_string_list(rel.get("aliases", [])),
            related=clean_string_list(rel.get("related", [])),
            definition=str(rel.get("description", "")).strip(),
        )

    for shape in shacl_candidate.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        target_class = str(shape.get("target_class", "")).strip()
        upsert_term(target_class, source="shacl.target_class")
        for prop in shape.get("properties", []):
            if not isinstance(prop, dict):
                continue
            path = str(prop.get("path", "")).strip()
            constraint = str(prop.get("constraint", "")).strip()
            if not path:
                continue
            params = prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {}
            definition = constraint
            if params:
                definition = f"{constraint} {json.dumps(params, sort_keys=True)}"
            upsert_term(
                f"{target_class}.{path}" if target_class else path,
                source="shacl.property",
                definition=definition,
            )

    for graph_data in prepared_graphs or []:
        for node in graph_data.get("nodes", []):
            if not isinstance(node, dict):
                continue
            label = str(node.get("label", "")).strip()
            if not label or label == "Document":
                continue
            properties = node.get("properties", {}) if isinstance(node.get("properties", {}), dict) else {}
            upsert_term(
                label,
                source="entity.label_observation",
                examples=[node_display_name(properties, node_id=str(node.get("id", "")).strip())],
            )
        for rel in graph_data.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            rel_type = str(rel.get("type", "")).strip()
            if rel_type and rel_type != "MENTIONS":
                upsert_term(rel_type, source="entity.relationship_observation")

    terms: List[Dict[str, Any]] = []
    for value in sorted(term_map.values(), key=lambda item: str(item.get("pref_label", "")).lower()):
        terms.append(
            {
                "pref_label": value["pref_label"],
                "alt_labels": sorted(value["alt_labels"], key=lambda alias: alias.lower()),
                "hidden_labels": sorted(value["hidden_labels"], key=lambda alias: alias.lower()),
                "broader": sorted(value["broader"], key=lambda alias: alias.lower()),
                "related": sorted(value["related"], key=lambda alias: alias.lower()),
                "definition": value.get("definition", ""),
                "sources": sorted(value["sources"]),
                "examples": sorted(value["examples"]),
            }
        )

    return {
        "schema_version": "vocabulary.v2",
        "profile": "skos",
        "terms": terms,
    }


def merge_string_lists(existing: Any, incoming: Any) -> List[str]:
    seen: Set[str] = set()
    merged: List[str] = []
    for value in clean_string_list(existing) + clean_string_list(incoming):
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return merged


def clean_string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def node_display_name(properties: Dict[str, Any], node_id: str = "") -> str:
    return str(
        properties.get("name")
        or properties.get("title")
        or properties.get("id")
        or properties.get("uri")
        or node_id
    ).strip()


def shacl_candidates_to_rule_profile(shacl_candidate: Dict[str, Any]) -> Dict[str, Any]:
    supported = {"required", "datatype", "enum", "range"}
    rules: List[Dict[str, Any]] = []
    for shape in shacl_candidate.get("shapes", []):
        label = str(shape.get("target_class", "")).strip()
        if not label:
            continue
        for prop in shape.get("properties", []):
            kind = str(prop.get("constraint", "")).strip()
            path = str(prop.get("path", "")).strip()
            if not path or kind not in supported:
                continue
            rules.append(
                {
                    "label": label,
                    "property_name": path,
                    "kind": kind,
                    "params": prop.get("params", {}) if isinstance(prop.get("params", {}), dict) else {},
                }
            )
    return {"schema_version": "rules.v1", "rules": rules}


def merge_rule_profiles(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for profile in [primary, secondary]:
        for rule in profile.get("rules", []):
            key = (
                str(rule.get("label", "")),
                str(rule.get("property_name", "")),
                str(rule.get("kind", "")),
                json.dumps(rule.get("params", {}), sort_keys=True),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "label": key[0],
                    "property_name": key[1],
                    "kind": key[2],
                    "params": rule.get("params", {}) if isinstance(rule.get("params", {}), dict) else {},
                }
            )
    return {"schema_version": "rules.v1", "rules": merged}


def summarize_relatedness(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    linked = sum(1 for item in records if item.get("is_related"))
    avg_score = 0.0 if total == 0 else sum(float(item.get("score", 0.0)) for item in records) / total
    embed_available = sum(1 for item in records if item.get("embedding_score") is not None)
    return {
        "total_records": total,
        "related_records": linked,
        "unrelated_records": max(total - linked, 0),
        "average_score": round(avg_score, 3),
        "embedding_evaluated_records": embed_available,
    }


def resolve_semantic_artifacts(
    policy: str,
    draft_ontology: Dict[str, Any],
    draft_shacl: Dict[str, Any],
    approved_artifacts: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    approved_ontology = approved_artifacts.get("ontology_candidate")
    approved_shacl = approved_artifacts.get("shacl_candidate")

    if policy == "draft_only":
        return (
            {
                "ontology_candidate": {"ontology_name": "", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            },
            {
                "policy": policy,
                "applied": "none",
                "status": "draft_pending_review",
                "warning": None,
            },
        )

    if policy == "approved_only":
        if isinstance(approved_ontology, dict) and isinstance(approved_shacl, dict):
            return (
                {"ontology_candidate": approved_ontology, "shacl_candidate": approved_shacl},
                {
                    "policy": policy,
                    "applied": "approved",
                    "status": "approved_applied",
                    "warning": None,
                },
            )
        return (
            {
                "ontology_candidate": {"ontology_name": "", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            },
            {
                "policy": policy,
                "applied": "none",
                "status": "approval_required",
                "warning": "approved_only policy requires approved_artifacts with ontology_candidate and shacl_candidate.",
            },
        )

    return (
        {"ontology_candidate": draft_ontology, "shacl_candidate": draft_shacl},
        {
            "policy": "auto",
            "applied": "draft",
            "status": "auto_applied",
            "warning": None,
        },
    )


__all__ = [
    "build_vocabulary_candidate",
    "clean_string_list",
    "merge_ontology_candidates",
    "merge_rule_profiles",
    "merge_shacl_candidates",
    "merge_string_lists",
    "node_display_name",
    "resolve_semantic_artifacts",
    "shacl_candidates_to_rule_profile",
    "summarize_relatedness",
]
