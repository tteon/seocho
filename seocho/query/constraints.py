from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from seocho.models import GraphTarget


DEFAULT_SEMANTIC_ARTIFACT_DIR = "outputs/semantic_artifacts"
ENTITY_PROPERTIES = (
    "name",
    "title",
    "id",
    "uri",
    "code",
    "symbol",
    "alias",
    "content_preview",
    "content",
    "memory_id",
)
COMMON_ALLOWED_PROPERTIES = {
    *ENTITY_PROPERTIES,
    "workspace_id",
    "status",
    "description",
    "summary",
    "type",
    "value",
    "created_at",
    "updated_at",
}


@dataclass(frozen=True)
class _CanonicalGraphTarget:
    graph_id: str
    database: str
    ontology_id: str
    vocabulary_profile: str


def _normalize_symbol(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _slugify_symbol(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "term"


def _clean_list(values: Iterable[Any]) -> List[str]:
    seen: set[str] = set()
    cleaned: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _merge_string_lists(existing: Sequence[Any], incoming: Sequence[Any]) -> List[str]:
    return _clean_list([*existing, *incoming])


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
                        "aliases": _clean_list(prop.get("aliases", [])),
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
            canonical = str(
                term.get("canonical")
                or term.get("pref_label")
                or term.get("name")
                or ""
            ).strip()
            if not canonical:
                continue
            existing = term_map.setdefault(
                canonical,
                {
                    "canonical": canonical,
                    "pref_label": str(term.get("pref_label") or canonical).strip() or canonical,
                    "uri": str(term.get("uri") or term.get("id") or "").strip(),
                    "aliases": [],
                    "alt_labels": [],
                    "hidden_labels": [],
                },
            )
            existing["aliases"] = _merge_string_lists(existing.get("aliases", []), term.get("aliases", []))
            existing["alt_labels"] = _merge_string_lists(existing.get("alt_labels", []), term.get("alt_labels", []))
            existing["hidden_labels"] = _merge_string_lists(existing.get("hidden_labels", []), term.get("hidden_labels", []))
            uri = str(term.get("uri") or term.get("id") or "").strip()
            if uri:
                existing["uri"] = uri

    return {
        "schema_version": schema_version,
        "profile": profile,
        "terms": list(term_map.values()),
    }


class SemanticConstraintSliceBuilder:
    """Build semantic query constraints from approved artifacts and graph targets."""

    def __init__(
        self,
        *,
        artifact_base_dir: Optional[str] = None,
        global_workspace_id: Optional[str] = None,
        graph_targets: Optional[Sequence[Any]] = None,
    ) -> None:
        self.artifact_base_dir = artifact_base_dir or os.getenv(
            "SEMANTIC_ARTIFACT_DIR",
            DEFAULT_SEMANTIC_ARTIFACT_DIR,
        )
        self.global_workspace_id = (
            global_workspace_id
            or os.getenv("VOCABULARY_GLOBAL_WORKSPACE_ID", "global").strip()
            or "global"
        )
        self._graph_targets: List[_CanonicalGraphTarget] = []
        for target in graph_targets or []:
            coerced = self._coerce_graph_target(target)
            if coerced is not None:
                self._graph_targets.append(coerced)

    def build_for_databases(
        self,
        databases: Sequence[str],
        *,
        workspace_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        return {
            str(database): self.build_for_database(str(database), workspace_id=workspace_id)
            for database in databases
        }

    def build_for_database(self, database: str, *, workspace_id: str) -> Dict[str, Any]:
        graph_target = self._find_graph_target(database)
        graph_id = graph_target.graph_id if graph_target is not None else database
        ontology_id = graph_target.ontology_id if graph_target is not None else database
        vocabulary_profile = (
            graph_target.vocabulary_profile
            if graph_target is not None
            else "vocabulary.v2"
        )

        artifact_payloads = self._load_matching_artifacts(
            workspace_id=workspace_id,
            ontology_id=ontology_id,
            graph_id=graph_id,
            database=database,
        )
        ontology_candidate = _merge_ontology_candidates(
            [payload.get("ontology_candidate") for payload in artifact_payloads]
        )
        shacl_candidate = _merge_shacl_candidates(
            [payload.get("shacl_candidate") for payload in artifact_payloads]
        )
        vocabulary_candidate = _merge_vocabulary_candidates(
            [payload.get("vocabulary_candidate") for payload in artifact_payloads]
        )

        allowed_labels = sorted(
            {
                str(item.get("name", "")).strip()
                for item in ontology_candidate.get("classes", [])
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
        )
        allowed_relationship_types = sorted(
            {
                str(item.get("type", "")).strip()
                for item in ontology_candidate.get("relationships", [])
                if isinstance(item, dict) and str(item.get("type", "")).strip()
            }
        )
        allowed_properties = set(COMMON_ALLOWED_PROPERTIES)
        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            for prop in cls.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                prop_name = str(prop.get("name", "")).strip()
                if prop_name:
                    allowed_properties.add(prop_name)
        for shape in shacl_candidate.get("shapes", []):
            if not isinstance(shape, dict):
                continue
            for prop in shape.get("properties", []):
                if not isinstance(prop, dict):
                    continue
                path = str(prop.get("path", "")).strip()
                if path:
                    allowed_properties.add(path)

        return {
            "graph_id": graph_id,
            "database": database,
            "ontology_id": ontology_id,
            "vocabulary_profile": vocabulary_profile,
            "artifact_ids": [
                str(payload.get("artifact_id", "")).strip()
                for payload in artifact_payloads
                if str(payload.get("artifact_id", "")).strip()
            ],
            "ontology_candidate": ontology_candidate,
            "shacl_candidate": shacl_candidate,
            "vocabulary_candidate": vocabulary_candidate,
            "allowed_labels": allowed_labels,
            "allowed_relationship_types": allowed_relationship_types,
            "allowed_properties": sorted(allowed_properties),
            "relation_aliases": self._build_relation_aliases(ontology_candidate),
            "label_aliases": self._build_label_aliases(ontology_candidate, vocabulary_candidate),
            "json_ld_context": self._build_json_ld_context(
                ontology_id=ontology_id,
                ontology_candidate=ontology_candidate,
                vocabulary_candidate=vocabulary_candidate,
            ),
            "constraint_strength": "semantic_layer" if artifact_payloads else "graph_metadata_only",
        }

    def _find_graph_target(self, database: str) -> Optional[_CanonicalGraphTarget]:
        for target in self._graph_targets:
            if target.database == database:
                return target
        return None

    @staticmethod
    def _coerce_graph_target(value: Any) -> Optional[_CanonicalGraphTarget]:
        if isinstance(value, GraphTarget):
            return _CanonicalGraphTarget(
                graph_id=value.graph_id,
                database=value.database,
                ontology_id=value.ontology_id,
                vocabulary_profile=value.vocabulary_profile,
            )
        if hasattr(value, "to_public_dict"):
            return SemanticConstraintSliceBuilder._coerce_graph_target(value.to_public_dict())
        if isinstance(value, dict):
            graph_id = str(value.get("graph_id", "")).strip()
            database = str(value.get("database", "")).strip()
            if not database:
                return None
            ontology_id = str(value.get("ontology_id", "")).strip() or database
            vocabulary_profile = str(value.get("vocabulary_profile", "")).strip() or "vocabulary.v2"
            return _CanonicalGraphTarget(
                graph_id=graph_id or database,
                database=database,
                ontology_id=ontology_id,
                vocabulary_profile=vocabulary_profile,
            )
        return None

    def _load_matching_artifacts(
        self,
        *,
        workspace_id: str,
        ontology_id: str,
        graph_id: str,
        database: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for current_workspace in {self.global_workspace_id, workspace_id}:
            for row in self._list_semantic_artifacts(current_workspace, status="approved"):
                artifact_id = str(row.get("artifact_id", "")).strip()
                if not artifact_id:
                    continue
                try:
                    payload = self._get_semantic_artifact(current_workspace, artifact_id)
                except FileNotFoundError:
                    continue
                if not self._artifact_matches(
                    payload,
                    ontology_id=ontology_id,
                    graph_id=graph_id,
                    database=database,
                ):
                    continue
                candidates.append(payload)
        candidates.sort(
            key=lambda payload: (
                str(payload.get("approved_at") or ""),
                str(payload.get("created_at") or ""),
                str(payload.get("artifact_id") or ""),
            )
        )
        return candidates

    def _workspace_dir(self, workspace_id: str) -> Path:
        return Path(self.artifact_base_dir) / workspace_id

    def _list_semantic_artifacts(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        workspace_path = self._workspace_dir(workspace_id)
        if not workspace_path.exists():
            return []

        rows: List[Dict[str, Any]] = []
        for path in workspace_path.glob("*.json"):
            with path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
            row = {
                "artifact_id": payload.get("artifact_id"),
                "workspace_id": payload.get("workspace_id"),
                "name": payload.get("name"),
                "created_at": payload.get("created_at"),
                "status": payload.get("status", "draft"),
                "approved_at": payload.get("approved_at"),
                "approved_by": payload.get("approved_by"),
                "deprecated_at": payload.get("deprecated_at"),
                "deprecated_by": payload.get("deprecated_by"),
            }
            if status and row["status"] != status:
                continue
            rows.append(row)
        rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return rows

    def _get_semantic_artifact(self, workspace_id: str, artifact_id: str) -> Dict[str, Any]:
        artifact_path = self._workspace_dir(workspace_id) / f"{artifact_id}.json"
        if not artifact_path.exists():
            raise FileNotFoundError(
                f"semantic artifact not found: workspace={workspace_id}, artifact_id={artifact_id}"
            )
        with artifact_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)

    @staticmethod
    def _artifact_matches(
        payload: Dict[str, Any],
        *,
        ontology_id: str,
        graph_id: str,
        database: str,
    ) -> bool:
        ontology_candidate = payload.get("ontology_candidate", {})
        ontology_name = str(ontology_candidate.get("ontology_name", "")).strip()
        artifact_name = str(payload.get("name", "")).strip()

        normalized_targets = {
            _normalize_symbol(ontology_id),
            _normalize_symbol(graph_id),
            _normalize_symbol(database),
        }
        normalized_targets.discard("")
        normalized_candidates = {
            _normalize_symbol(ontology_name),
            _normalize_symbol(artifact_name),
        }
        normalized_candidates.discard("")
        if not normalized_targets:
            return True
        return bool(normalized_targets & normalized_candidates)

    @staticmethod
    def _build_relation_aliases(ontology_candidate: Dict[str, Any]) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        for rel in ontology_candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            relation_type = str(rel.get("type", "")).strip()
            if not relation_type:
                continue
            for candidate in [relation_type, *rel.get("aliases", []), *rel.get("related", [])]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = relation_type
        return aliases

    @staticmethod
    def _build_label_aliases(
        ontology_candidate: Dict[str, Any],
        vocabulary_candidate: Dict[str, Any],
    ) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            canonical = str(cls.get("name", "")).strip()
            if not canonical:
                continue
            for candidate in [canonical, *cls.get("aliases", []), *cls.get("related", [])]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = canonical

        for term in vocabulary_candidate.get("terms", []):
            if not isinstance(term, dict):
                continue
            canonical = str(
                term.get("canonical")
                or term.get("pref_label")
                or term.get("name")
                or ""
            ).strip()
            if not canonical:
                continue
            for candidate in [
                canonical,
                *term.get("aliases", []),
                *term.get("alt_labels", []),
                *term.get("hidden_labels", []),
            ]:
                normalized = _normalize_symbol(candidate)
                if normalized:
                    aliases[normalized] = canonical
        return aliases

    @staticmethod
    def _build_json_ld_context(
        *,
        ontology_id: str,
        ontology_candidate: Dict[str, Any],
        vocabulary_candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = f"seocho://semantic/{_slugify_symbol(ontology_id)}/"
        context: Dict[str, Any] = {"@vocab": base}

        def register(term: Any, iri: Optional[str] = None) -> None:
            term_text = str(term).strip()
            if not term_text:
                return
            context[term_text] = iri or f"{base}{_slugify_symbol(term_text)}"

        for cls in ontology_candidate.get("classes", []):
            if not isinstance(cls, dict):
                continue
            register(cls.get("name", ""))
            for alias in cls.get("aliases", []):
                register(alias)
        for rel in ontology_candidate.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            register(rel.get("type", ""))
            for alias in rel.get("aliases", []):
                register(alias)
        for term in vocabulary_candidate.get("terms", []):
            if not isinstance(term, dict):
                continue
            iri = str(term.get("uri") or term.get("id") or "").strip() or None
            register(term.get("pref_label") or term.get("canonical") or term.get("name"), iri)
            for alias in [
                *term.get("alt_labels", []),
                *term.get("hidden_labels", []),
                *term.get("aliases", []),
            ]:
                register(alias, iri)
        return context
