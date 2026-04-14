from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .answering import build_evidence_bundle, infer_question_intent
from .constraints import SemanticConstraintSliceBuilder
from .contracts import CypherPlan, InsufficiencyAssessment
from .cypher_validator import CypherQueryValidator
from .insufficiency import QueryInsufficiencyClassifier
from .run_registry import RunMetadataRegistry
from .strategy_chooser import ExecutionStrategyChooser, IntentSupportValidator

logger = logging.getLogger(__name__)

RDF_HINTS = {
    "rdf",
    "rdfs",
    "owl",
    "shacl",
    "sparql",
    "triple",
    "ontology",
    "uri",
    "class",
    "instance",
}

LPG_HINTS = {
    "cypher",
    "node",
    "edge",
    "path",
    "neighbor",
    "graph",
    "community",
    "relationship",
}

STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "do",
    "does",
    "did",
    "what",
    "which",
    "who",
    "whom",
    "where",
    "when",
    "why",
    "how",
    "tell",
    "show",
    "about",
    "please",
}

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

QUESTION_LABEL_HINTS = {
    "company": {"company", "organization", "org", "enterprise", "firm"},
    "person": {"person", "human", "individual", "employee", "ceo", "founder"},
    "product": {"product", "service", "offering"},
    "event": {"event", "incident", "meeting"},
    "document": {"document", "section", "chunk"},
    "ontology": {"ontology", "class", "property", "concept"},
}

DEFAULT_SEMANTIC_ARTIFACT_DIR = "outputs/semantic_artifacts"


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _parse_cypher_rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, str) and raw.startswith("Error"):
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


class OntologyHintStore:
    """In-memory ontology hint store with lightweight alias/label maps."""

    def __init__(self, path: str = "output/ontology_hints.json"):
        self.path = path
        self.aliases: Dict[str, str] = {}
        self.label_keywords: Dict[str, Set[str]] = {}
        self.loaded: bool = False
        self.load()

    def load(self) -> None:
        self.aliases = {}
        self.label_keywords = {}
        self.loaded = False

        if not self.path or not os.path.exists(self.path):
            logger.info("Ontology hints file not found: %s", self.path)
            return

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("Failed to load ontology hints (%s): %s", self.path, exc)
            return

        alias_map = payload.get("aliases", {})
        if isinstance(alias_map, dict):
            for src, dst in alias_map.items():
                src_norm = _normalize(str(src))
                dst_text = str(dst).strip()
                if src_norm and dst_text:
                    self.aliases[src_norm] = dst_text

        label_map = payload.get("label_keywords", {})
        if isinstance(label_map, dict):
            for label, keywords in label_map.items():
                label_key = _normalize(str(label))
                if not label_key:
                    continue
                bucket: Set[str] = set()
                if isinstance(keywords, list):
                    for keyword in keywords:
                        token = _normalize(str(keyword))
                        if token:
                            bucket.add(token)
                if bucket:
                    self.label_keywords[label_key] = bucket

        self.loaded = bool(self.aliases or self.label_keywords)
        logger.info(
            "Loaded ontology hints: aliases=%d label_groups=%d",
            len(self.aliases),
            len(self.label_keywords),
        )

    def resolve_alias(self, entity_text: str) -> str:
        key = _normalize(entity_text)
        return self.aliases.get(key, entity_text)

    def infer_label_hints(self, question: str) -> Set[str]:
        q_norm = _normalize(question)
        hits: Set[str] = set()
        if not q_norm:
            return hits

        for label, keywords in self.label_keywords.items():
            if any(keyword in q_norm for keyword in keywords):
                hits.add(label)
        return hits

    def to_summary(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "loaded": self.loaded,
            "alias_count": len(self.aliases),
            "label_group_count": len(self.label_keywords),
        }


class ManagedVocabularyResolver:
    """Resolve query aliases from approved semantic artifact vocabulary."""

    def __init__(
        self,
        *,
        base_dir: str | None = None,
        global_workspace_id: str | None = None,
    ) -> None:
        self.base_dir = base_dir or os.getenv("SEMANTIC_ARTIFACT_DIR", DEFAULT_SEMANTIC_ARTIFACT_DIR)
        configured_global = os.getenv("VOCABULARY_GLOBAL_WORKSPACE_ID", "global").strip()
        self.global_workspace_id = global_workspace_id or configured_global or "global"
        self.enabled = os.getenv("VOCABULARY_RESOLVER_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self._cache: Dict[str, Dict[str, Any]] = {}

    def resolve_alias(self, entity_text: str, workspace_id: str = "default") -> str:
        if not self.enabled:
            return entity_text
        normalized = _normalize(entity_text)
        if not normalized:
            return entity_text
        payload = self._workspace_payload(workspace_id)
        return str(payload["aliases"].get(normalized, entity_text))

    def to_summary(self, workspace_id: str = "default") -> Dict[str, Any]:
        payload = self._workspace_payload(workspace_id)
        return {
            "enabled": self.enabled,
            "base_dir": self.base_dir,
            "workspace_id": workspace_id,
            "global_workspace_id": self.global_workspace_id,
            "alias_count": len(payload["aliases"]),
            "approved_artifact_counts": payload["approved_artifact_counts"],
        }

    def clear_cache(self) -> None:
        self._cache = {}

    def _workspace_payload(self, workspace_id: str) -> Dict[str, Any]:
        key = str(workspace_id or "default")
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        global_payload = self._collect_workspace_aliases(self.global_workspace_id)
        workspace_payload = (
            self._collect_workspace_aliases(key)
            if key != self.global_workspace_id
            else {"aliases": {}, "approved_count": 0}
        )

        aliases: Dict[str, str] = {}
        aliases.update(global_payload["aliases"])
        aliases.update(workspace_payload["aliases"])

        payload = {
            "aliases": aliases,
            "approved_artifact_counts": {
                "global": int(global_payload["approved_count"]),
                "workspace": int(workspace_payload["approved_count"]),
            },
        }
        self._cache[key] = payload
        return payload

    def _collect_workspace_aliases(self, workspace_id: str) -> Dict[str, Any]:
        approved_rows = self._list_semantic_artifacts(workspace_id, status="approved")
        if not approved_rows:
            return {"aliases": {}, "approved_count": 0}

        aliases: Dict[str, str] = {}
        ordered_rows = sorted(
            approved_rows,
            key=lambda row: (
                str(row.get("approved_at") or ""),
                str(row.get("created_at") or ""),
                str(row.get("artifact_id") or ""),
            ),
        )
        for row in ordered_rows:
            artifact_id = str(row.get("artifact_id") or "").strip()
            if not artifact_id:
                continue
            try:
                payload = self._get_semantic_artifact(workspace_id, artifact_id)
            except FileNotFoundError:
                logger.warning(
                    "Approved semantic artifact missing on disk: workspace=%s artifact_id=%s",
                    workspace_id,
                    artifact_id,
                )
                continue
            self._merge_aliases_from_artifact(payload, aliases)
        return {"aliases": aliases, "approved_count": len(ordered_rows)}

    def _workspace_dir(self, workspace_id: str) -> Path:
        return Path(self.base_dir) / workspace_id

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

    def _merge_aliases_from_artifact(
        self,
        payload: Dict[str, Any],
        aliases: Dict[str, str],
    ) -> None:
        vocab_candidate = payload.get("vocabulary_candidate")
        if isinstance(vocab_candidate, dict):
            for term in vocab_candidate.get("terms", []):
                if not isinstance(term, dict):
                    continue
                canonical = str(
                    term.get("canonical")
                    or term.get("pref_label")
                    or term.get("name")
                    or ""
                ).strip()
                term_aliases = term.get("aliases", [])
                if not isinstance(term_aliases, list):
                    term_aliases = []
                alt_labels = term.get("alt_labels", [])
                if not isinstance(alt_labels, list):
                    alt_labels = []
                hidden_labels = term.get("hidden_labels", [])
                if not isinstance(hidden_labels, list):
                    hidden_labels = []
                self._register_term(aliases, canonical, [*term_aliases, *alt_labels, *hidden_labels])

        ontology_candidate = payload.get("ontology_candidate", {})
        if isinstance(ontology_candidate, dict):
            for cls in ontology_candidate.get("classes", []):
                if not isinstance(cls, dict):
                    continue
                canonical = str(cls.get("name", "")).strip()
                cls_aliases = cls.get("aliases", [])
                if not isinstance(cls_aliases, list):
                    cls_aliases = []
                self._register_term(aliases, canonical, cls_aliases)

            for rel in ontology_candidate.get("relationships", []):
                if not isinstance(rel, dict):
                    continue
                canonical = str(rel.get("type", "")).strip()
                rel_aliases = rel.get("aliases", [])
                if not isinstance(rel_aliases, list):
                    rel_aliases = []
                self._register_term(aliases, canonical, rel_aliases)

        shacl_candidate = payload.get("shacl_candidate", {})
        if isinstance(shacl_candidate, dict):
            for shape in shacl_candidate.get("shapes", []):
                if not isinstance(shape, dict):
                    continue
                self._register_term(aliases, str(shape.get("target_class", "")).strip(), [])

    @staticmethod
    def _register_term(
        aliases: Dict[str, str],
        canonical: str,
        term_aliases: List[Any],
    ) -> None:
        canonical_text = str(canonical).strip()
        if not canonical_text:
            return
        values: List[str] = [canonical_text]
        for item in term_aliases:
            alias = str(item).strip()
            if alias:
                values.append(alias)
        for value in values:
            normalized = _normalize(value)
            if normalized:
                aliases[normalized] = canonical_text


@dataclass(frozen=True)
class SemanticProfilePackage:
    profile_id: str
    ontology_ids: tuple[str, ...]
    intent_id: str
    query_kind: str
    relation_priority: tuple[str, ...]
    notes: str = ""


_PACKAGES: tuple[SemanticProfilePackage, ...] = (
    SemanticProfilePackage(
        profile_id="baseline.relationship.v1",
        ontology_ids=("baseline", "kgnormal", "neo4j"),
        intent_id="relationship_lookup",
        query_kind="relationship_lookup",
        relation_priority=("RELATES_TO", "USES", "WORKS_WITH", "OWNS"),
        notes="Deterministic relationship lookup for baseline graphs.",
    ),
    SemanticProfilePackage(
        profile_id="baseline.responsibility.v1",
        ontology_ids=("baseline", "kgnormal", "neo4j"),
        intent_id="responsibility_lookup",
        query_kind="responsibility_lookup",
        relation_priority=("MANAGES", "OPERATES", "LEADS", "OWNS"),
        notes="Deterministic responsibility lookup for baseline graphs.",
    ),
    SemanticProfilePackage(
        profile_id="fibo.relationship.v1",
        ontology_ids=("fibo", "kgfibo", "finance"),
        intent_id="relationship_lookup",
        query_kind="relationship_lookup",
        relation_priority=("RELATES_TO", "ISSUED_BY", "CONTROLS", "OWNS"),
        notes="Deterministic relationship lookup for FIBO-aligned graphs.",
    ),
    SemanticProfilePackage(
        profile_id="fibo.responsibility.v1",
        ontology_ids=("fibo", "kgfibo", "finance"),
        intent_id="responsibility_lookup",
        query_kind="responsibility_lookup",
        relation_priority=("OWNS", "CONTROLS", "OPERATES", "MANAGES"),
        notes="Deterministic responsibility lookup for FIBO-aligned graphs.",
    ),
)


def select_semantic_profile_package(
    *,
    intent_id: str,
    constraint_slice: Dict[str, Any],
) -> Optional[SemanticProfilePackage]:
    normalized_intent = str(intent_id or "").strip()
    ontology_candidates = {
        str(constraint_slice.get("ontology_id", "")).strip().lower(),
        str(constraint_slice.get("graph_id", "")).strip().lower(),
        str(constraint_slice.get("database", "")).strip().lower(),
    }
    ontology_candidates.discard("")
    for package in _PACKAGES:
        if package.intent_id != normalized_intent:
            continue
        if ontology_candidates & {item.lower() for item in package.ontology_ids}:
            return package
    return None


def apply_profile_relation_priority(
    *,
    package: Optional[SemanticProfilePackage],
    relation_types: Sequence[str],
    constraint_slice: Dict[str, Any],
) -> List[str]:
    if package is None:
        return list(relation_types)
    allowed = {
        str(item).strip()
        for item in constraint_slice.get("allowed_relationship_types", [])
        if str(item).strip()
    }
    if not allowed:
        return [relation for relation in package.relation_priority if relation]

    prioritized = [relation for relation in package.relation_priority if relation in allowed]
    passthrough = [relation for relation in relation_types if relation in allowed and relation not in prioritized]
    if prioritized:
        return prioritized + passthrough
    return passthrough


class SemanticEntityResolver:
    """Resolve question entities against graph entities."""

    def __init__(
        self,
        connector: Any,
        fulltext_index_hint: str = "entity_fulltext",
        candidate_limit: int = 5,
        ontology_hint_store: Optional[OntologyHintStore] = None,
        vocabulary_resolver: Optional[ManagedVocabularyResolver] = None,
    ):
        self.connector = connector
        self.fulltext_index_hint = fulltext_index_hint
        self.candidate_limit = candidate_limit
        self.ontology_hint_store = ontology_hint_store or OntologyHintStore(
            path=os.getenv("ONTOLOGY_HINTS_PATH", "output/ontology_hints.json")
        )
        self.vocabulary_resolver = vocabulary_resolver or ManagedVocabularyResolver()

    def extract_question_entities(self, question: str) -> List[str]:
        quoted = [m.group(1).strip() for m in re.finditer(r'"([^"]+)"', question)]
        single_quoted = [m.group(1).strip() for m in re.finditer(r"'([^']+)'", question)]
        caps = [
            m.group(0).strip()
            for m in re.finditer(
                r"\b(?:[A-Z][a-zA-Z0-9&.-]{1,}|[A-Z]{2,})(?:\s+[A-Z][a-zA-Z0-9&.-]{1,})*\b",
                question,
            )
        ]

        entities: List[str] = []
        seen: Set[str] = set()
        for value in quoted + single_quoted + caps:
            cleaned = self._clean_span(value)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen or key in STOPWORDS:
                continue
            seen.add(key)
            entities.append(cleaned)

        if not entities:
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9&._-]{2,}", question):
                key = token.lower()
                if key in STOPWORDS or key.isdigit():
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entities.append(token)
                if len(entities) >= 3:
                    break
        return entities

    def resolve(
        self,
        question: str,
        databases: Sequence[str],
        workspace_id: str = "default",
    ) -> Dict[str, Any]:
        entities = self.extract_question_entities(question)
        label_hints = self._infer_label_hints(question)
        label_hints.update(self.ontology_hint_store.infer_label_hints(question))
        fulltext_indexes = self._discover_fulltext_indexes(databases)

        matches: Dict[str, List[Dict[str, Any]]] = {}
        unresolved: List[str] = []
        vocabulary_resolved: Dict[str, str] = {}
        alias_resolved: Dict[str, str] = {}

        for entity in entities:
            vocabulary_text = self.vocabulary_resolver.resolve_alias(entity, workspace_id=workspace_id)
            resolved_text = self.ontology_hint_store.resolve_alias(vocabulary_text)
            vocabulary_resolved[entity] = vocabulary_text
            alias_resolved[entity] = resolved_text
            candidates: List[Dict[str, Any]] = []
            for db_name in databases:
                db_candidates = self._query_fulltext_candidates(
                    db_name=db_name,
                    entity_text=resolved_text,
                    indexes=fulltext_indexes.get(db_name, []),
                    workspace_id=workspace_id,
                )
                if not db_candidates:
                    db_candidates = self._query_contains_candidates(
                        db_name=db_name,
                        entity_text=resolved_text,
                        workspace_id=workspace_id,
                    )
                candidates.extend(db_candidates)

            ranked = self._rank_and_dedup(
                entity_text=entity,
                resolved_text=resolved_text,
                candidates=candidates,
                label_hints=label_hints,
            )
            if ranked:
                matches[entity] = ranked
            else:
                unresolved.append(entity)

        intent = infer_question_intent(question, entities)
        semantic_context = {
            "entities": entities,
            "label_hints": sorted(label_hints),
            "vocabulary_resolved": vocabulary_resolved,
            "alias_resolved": alias_resolved,
            "matches": matches,
            "unresolved_entities": unresolved,
            "vocabulary_hints": self.vocabulary_resolver.to_summary(workspace_id),
            "ontology_hints": self.ontology_hint_store.to_summary(),
            "intent": intent,
        }
        semantic_context["evidence_bundle_preview"] = build_evidence_bundle(
            question=question,
            semantic_context=semantic_context,
            matched_entities=entities,
        )
        return semantic_context

    def _discover_fulltext_indexes(self, databases: Sequence[str]) -> Dict[str, List[str]]:
        by_db: Dict[str, List[str]] = {}
        for db_name in databases:
            indexes: List[str] = []
            candidates = [
                "SHOW FULLTEXT INDEXES YIELD name, state WHERE state = 'ONLINE' RETURN name",
                "SHOW INDEXES YIELD name, type, state WHERE type = 'FULLTEXT' AND state = 'ONLINE' RETURN name",
            ]
            for query in candidates:
                rows = self._run_query(db_name, query, params=None)
                if rows:
                    indexes = [str(row.get("name", "")).strip() for row in rows if row.get("name")]
                    if indexes:
                        break
            if self.fulltext_index_hint and self.fulltext_index_hint not in indexes:
                indexes.insert(0, self.fulltext_index_hint)
            by_db[db_name] = [idx for idx in indexes if idx]
        return by_db

    def _query_fulltext_candidates(
        self,
        db_name: str,
        entity_text: str,
        indexes: Sequence[str],
        workspace_id: str,
    ) -> List[Dict[str, Any]]:
        query = """
        CALL db.index.fulltext.queryNodes($index_name, $query)
        YIELD node, score
        WITH node, score
        WHERE coalesce(node.workspace_id, $workspace_id) = $workspace_id
          AND coalesce(node.status, 'active') <> 'archived'
        RETURN elementId(node) AS node_id,
               labels(node) AS labels,
               coalesce(node.name, node.title, node.id, node.uri, elementId(node)) AS display_name,
               coalesce(node.source_id, '') AS source_id,
               coalesce(node.memory_id, node.source_id, '') AS memory_id,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        for index_name in indexes:
            rows = self._run_query(
                db_name,
                query,
                params={
                    "index_name": index_name,
                    "query": entity_text,
                    "limit": self.candidate_limit,
                    "workspace_id": workspace_id,
                },
            )
            if not rows:
                continue

            candidates: List[Dict[str, Any]] = []
            for row in rows:
                candidates.append(
                    {
                        "database": db_name,
                        "entity_text": entity_text,
                        "node_id": row.get("node_id"),
                        "labels": row.get("labels", []),
                        "display_name": row.get("display_name", ""),
                        "source_id": row.get("source_id", ""),
                        "memory_id": row.get("memory_id", ""),
                        "base_score": float(row.get("score", 0.0) or 0.0),
                        "source": "fulltext",
                        "index_name": index_name,
                    }
                )
            if candidates:
                return candidates
        return []

    def _query_contains_candidates(self, db_name: str, entity_text: str, workspace_id: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE coalesce(n.workspace_id, $workspace_id) = $workspace_id
          AND coalesce(n.status, 'active') <> 'archived'
          AND any(key IN $properties
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN elementId(n) AS node_id,
               labels(n) AS labels,
               coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS display_name,
               coalesce(n.source_id, '') AS source_id,
               coalesce(n.memory_id, n.source_id, '') AS memory_id
        LIMIT $limit
        """
        rows = self._run_query(
            db_name,
            query,
            params={
                "properties": list(ENTITY_PROPERTIES),
                "query": entity_text,
                "limit": self.candidate_limit,
                "workspace_id": workspace_id,
            },
        )
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            display_name = str(row.get("display_name", ""))
            lexical = self._lexical_similarity(entity_text, display_name)
            candidates.append(
                {
                    "database": db_name,
                    "entity_text": entity_text,
                    "node_id": row.get("node_id"),
                    "labels": row.get("labels", []),
                    "display_name": display_name,
                    "source_id": row.get("source_id", ""),
                    "memory_id": row.get("memory_id", ""),
                    "base_score": lexical,
                    "source": "contains",
                    "index_name": None,
                }
            )
        return candidates

    def _rank_and_dedup(
        self,
        entity_text: str,
        resolved_text: str,
        candidates: Sequence[Dict[str, Any]],
        label_hints: Set[str],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, Any]] = set()

        normalized_entity = self._normalize(entity_text)
        normalized_resolved = self._normalize(resolved_text)
        for candidate in candidates:
            db_name = str(candidate.get("database", ""))
            node_id = candidate.get("node_id")
            dedup_key = (db_name, node_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            display_name = str(candidate.get("display_name", ""))
            normalized_display = self._normalize(display_name)
            lexical = self._lexical_similarity(normalized_entity, normalized_display)
            base_score = float(candidate.get("base_score", 0.0))
            label_boost = self._label_boost(candidate.get("labels", []), label_hints)
            exact_boost = 0.2 if normalized_entity == normalized_display else 0.0
            alias_boost = 0.12 if normalized_resolved == normalized_display else 0.0
            final_score = base_score + lexical + label_boost + exact_boost + alias_boost

            ranked.append(
                {
                    **candidate,
                    "lexical_score": round(lexical, 4),
                    "label_boost": round(label_boost, 4),
                    "alias_boost": round(alias_boost, 4),
                    "final_score": round(final_score, 4),
                }
            )

        ranked.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
        top_candidates = ranked[: self.candidate_limit]

        if len(top_candidates) > 0:
            best_score = top_candidates[0].get("final_score", 0.0)
            if len(top_candidates) > 1:
                runner_up = top_candidates[1].get("final_score", 0.0)
                gap = best_score - runner_up
                top_candidates[0]["is_confident"] = gap > 0.15
            else:
                top_candidates[0]["is_confident"] = True

        return top_candidates

    def _run_query(
        self,
        db_name: str,
        query: str,
        params: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raw = self.connector.run_cypher(query=query, database=db_name, params=params)
        if isinstance(raw, str) and raw.startswith("Error"):
            logger.debug("Cypher error [%s]: %s", db_name, raw)
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            logger.debug("Non-JSON query output [%s]: %s", db_name, str(raw)[:160])
            return []
        if not isinstance(parsed, list):
            return []
        return parsed

    @staticmethod
    def _clean_span(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        cleaned = cleaned.strip(".,:;!?()[]{}")
        return cleaned

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @staticmethod
    def _lexical_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _infer_label_hints(question: str) -> Set[str]:
        q = question.lower()
        hints: Set[str] = set()
        for _, tokens in QUESTION_LABEL_HINTS.items():
            if any(token in q for token in tokens):
                hints.update(tokens)
        return hints

    @staticmethod
    def _label_boost(labels: Sequence[str], label_hints: Set[str]) -> float:
        if not labels or not label_hints:
            return 0.0
        normalized_labels = {re.sub(r"[^a-z0-9]+", "", str(label).lower()) for label in labels}
        normalized_hints = {re.sub(r"[^a-z0-9]+", "", hint.lower()) for hint in label_hints}
        if normalized_labels & normalized_hints:
            return 0.15
        return 0.0


class QueryRouterAgent:
    """Route question to LPG, RDF, or hybrid path."""

    def route(self, question: str) -> str:
        q = question.lower()
        has_rdf = any(token in q for token in RDF_HINTS)
        has_lpg = any(token in q for token in LPG_HINTS)
        if has_rdf and has_lpg:
            return "hybrid"
        if has_rdf:
            return "rdf"
        return "lpg"


class LPGAgent:
    """LPG query agent with semantic-layer-constrained Cypher planning."""

    def __init__(
        self,
        connector: Any,
        result_limit: int = 20,
        graph_targets: Optional[Sequence[Any]] = None,
    ):
        self.connector = connector
        self.result_limit = result_limit
        self.constraint_builder = SemanticConstraintSliceBuilder(graph_targets=graph_targets)
        self.validator = CypherQueryValidator()
        self.classifier = QueryInsufficiencyClassifier()
        self.support_validator = IntentSupportValidator()

    def preview_support(
        self,
        semantic_context: Dict[str, Any],
        constraint_slices: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        intent = semantic_context.get("intent", {})
        preview_bundle = semantic_context.get("evidence_bundle_preview", {})
        ranked_matches: List[Dict[str, Any]] = []
        support_candidates: List[Dict[str, Any]] = []

        for question_entity, candidates in semantic_context.get("matches", {}).items():
            if not isinstance(candidates, list):
                continue
            for candidate in candidates[:2]:
                if not isinstance(candidate, dict):
                    continue
                db_name = str(candidate.get("database", "")).strip()
                if not db_name:
                    continue
                constraint_slice = constraint_slices.get(db_name, {})
                support = self.support_validator.assess_candidate(
                    question_entity=question_entity,
                    candidate=candidate,
                    intent=intent,
                    constraint_slice=constraint_slice,
                    preview_bundle=preview_bundle,
                )
                annotated = dict(candidate)
                annotated["question_entity"] = question_entity
                annotated["support_assessment"] = support
                ranked_matches.append(annotated)
                support_candidates.append(support)

        ranked_matches.sort(
            key=lambda item: (
                bool(item.get("support_assessment", {}).get("supported")),
                float(item.get("support_assessment", {}).get("coverage", 0.0) or 0.0),
                float(item.get("final_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        support_candidates.sort(
            key=lambda item: (
                bool(item.get("supported")),
                float(item.get("coverage", 0.0) or 0.0),
                float(item.get("confidence", 0.0) or 0.0),
            ),
            reverse=True,
        )

        semantic_context["support_candidates"] = support_candidates[:6]
        semantic_context["preflight_support_assessment"] = (
            support_candidates[0]
            if support_candidates
            else self.support_validator.empty_assessment(intent)
        )
        semantic_context.setdefault(
            "support_assessment",
            semantic_context["preflight_support_assessment"],
        )
        semantic_context["cross_graph_analysis"] = self._summarize_cross_graph_support(ranked_matches)
        return ranked_matches[:6]

    @staticmethod
    def _summarize_cross_graph_support(
        ranked_matches: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        best_by_database: Dict[str, Dict[str, Any]] = {}
        for item in ranked_matches:
            database = str(item.get("database", "")).strip()
            if not database or database in best_by_database:
                continue
            best_by_database[database] = item

        if len(best_by_database) <= 1:
            return {
                "graph_count": len(best_by_database),
                "compared_databases": sorted(best_by_database),
                "recommended_advanced": False,
                "reason": "",
                "support_variance": 0.0,
                "entity_disagreement": False,
                "relation_disagreement": False,
                "support_mismatch": False,
            }

        supports = [
            float(item.get("support_assessment", {}).get("coverage", 0.0) or 0.0)
            for item in best_by_database.values()
        ]
        entity_names = {
            str(item.get("display_name") or item.get("question_entity") or "").strip().lower()
            for item in best_by_database.values()
            if str(item.get("display_name") or item.get("question_entity") or "").strip()
        }
        relation_sets = {
            tuple(
                sorted(
                    str(rel).strip()
                    for rel in item.get("support_assessment", {}).get("matched_relations", [])
                    if str(rel).strip()
                )
            )
            for item in best_by_database.values()
        }
        support_statuses = {
            str(item.get("support_assessment", {}).get("status", "")).strip()
            for item in best_by_database.values()
            if str(item.get("support_assessment", {}).get("status", "")).strip()
        }
        support_variance = round(max(supports) - min(supports), 4) if supports else 0.0
        entity_disagreement = len(entity_names) > 1
        relation_disagreement = len(relation_sets) > 1
        support_mismatch = len(support_statuses) > 1 or support_variance >= 0.35
        recommended_advanced = entity_disagreement or relation_disagreement or support_mismatch
        if entity_disagreement:
            reason = "graph scopes resolve different anchor entities"
        elif relation_disagreement:
            reason = "graph scopes support different relation paths"
        elif support_mismatch:
            reason = "graph scopes show materially different support levels"
        else:
            reason = ""

        return {
            "graph_count": len(best_by_database),
            "compared_databases": sorted(best_by_database),
            "recommended_advanced": recommended_advanced,
            "reason": reason,
            "support_variance": support_variance,
            "entity_disagreement": entity_disagreement,
            "relation_disagreement": relation_disagreement,
            "support_mismatch": support_mismatch,
        }

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
        *,
        workspace_id: str = "default",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
        constraint_slices: Optional[Dict[str, Dict[str, Any]]] = None,
        ranked_matches: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        top_matches = list(ranked_matches) if ranked_matches is not None else self._top_entity_matches(
            semantic_context,
            constraint_slices or {},
        )
        if not top_matches:
            return {
                "mode": "lpg",
                "summary": "No resolved entity. Returned graph label distribution.",
                "records": self._label_distribution(databases),
                "reasoning": {
                    "requested": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "attempt_count": 0,
                    "repair_trace": [],
                    "self_reflection_used": False,
                },
                "support_assessment": semantic_context.get("support_assessment", {}),
                "execution_strategy": semantic_context.get("strategy_decision", {}),
            }

        constraint_slices = constraint_slices or self.constraint_builder.build_for_databases(
            databases,
            workspace_id=workspace_id,
        )
        records: List[Dict[str, Any]] = []
        repair_trace: List[Dict[str, Any]] = []
        best_assessment: Optional[InsufficiencyAssessment] = None
        best_bundle: Optional[Dict[str, Any]] = None
        best_support_assessment: Optional[Dict[str, Any]] = None
        selected_constraint_slice: Optional[Dict[str, Any]] = None
        attempt_limit = 1 + max(0, int(repair_budget or 0)) if reasoning_mode else 1

        for item in top_matches:
            db_name = str(item.get("database", "")).strip()
            node_id = item.get("node_id")
            if not db_name or node_id is None:
                continue
            constraint_slice = constraint_slices.get(db_name) or self.constraint_builder.build_for_database(
                db_name,
                workspace_id=workspace_id,
            )
            execution = self._execute_with_repair(
                question=question,
                semantic_context=semantic_context,
                anchor_match=item,
                constraint_slice=constraint_slice,
                attempt_limit=attempt_limit,
            )
            repair_trace.extend(execution["repair_trace"])
            if execution["records"]:
                records.extend(execution["records"])
            if best_assessment is None or (
                execution["assessment"].sufficient and not best_assessment.sufficient
            ) or execution["assessment"].row_count > best_assessment.row_count:
                best_assessment = execution["assessment"]
                best_bundle = execution["evidence_bundle"]
                best_support_assessment = execution["support_assessment"]
                selected_constraint_slice = constraint_slice
            if execution["assessment"].sufficient:
                break

        unique_anchors = {
            (
                str(item.get("database", "")).strip(),
                str(item.get("anchor_entity", "")).strip(),
            )
            for item in repair_trace
            if str(item.get("anchor_entity", "")).strip()
        }
        self_reflection_used = len(unique_anchors) > 1

        if not records:
            summary = "No grounded LPG result satisfied the semantic-layer constraints. Returned label distribution."
            return {
                "mode": "lpg",
                "summary": summary,
                "records": self._label_distribution(databases),
                "reasoning": {
                    "requested": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "attempt_count": len(repair_trace),
                    "repair_trace": repair_trace,
                    "constraint_slice": self._summarize_constraint_slice(selected_constraint_slice),
                    "self_reflection_used": self_reflection_used,
                    "insufficiency": (
                        {
                            "reason": best_assessment.reason,
                            "missing_slots": list(best_assessment.missing_slots),
                            "row_count": best_assessment.row_count,
                        }
                        if best_assessment is not None
                        else {"reason": "no_executable_plan", "missing_slots": [], "row_count": 0}
                    ),
                },
                "evidence_bundle": best_bundle or semantic_context.get("evidence_bundle_preview", {}),
                "support_assessment": best_support_assessment or semantic_context.get("support_assessment", {}),
                "execution_strategy": semantic_context.get("strategy_decision", {}),
            }

        best_reason = best_assessment.reason if best_assessment is not None else "sufficient"
        if reasoning_mode and len(repair_trace) > len(top_matches):
            summary = "Reasoning mode repaired an initially insufficient constrained Cypher plan."
        else:
            summary = "Resolved entities were expanded through semantic-layer-constrained LPG queries."
        return {
            "mode": "lpg",
            "summary": summary,
            "records": records,
            "reasoning": {
                "requested": reasoning_mode,
                "repair_budget": max(0, int(repair_budget or 0)),
                "attempt_count": len(repair_trace),
                "repair_trace": repair_trace,
                "constraint_slice": self._summarize_constraint_slice(selected_constraint_slice),
                "terminal_reason": best_reason,
                "self_reflection_used": self_reflection_used,
            },
            "evidence_bundle": best_bundle or semantic_context.get("evidence_bundle_preview", {}),
            "support_assessment": best_support_assessment or semantic_context.get("support_assessment", {}),
            "execution_strategy": semantic_context.get("strategy_decision", {}),
        }

    def _top_entity_matches(
        self,
        semantic_context: Dict[str, Any],
        constraint_slices: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ranked_matches = self.preview_support(semantic_context, constraint_slices)
        return ranked_matches[:3]

    def _execute_with_repair(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        attempt_limit: int,
    ) -> Dict[str, Any]:
        intent = semantic_context.get("intent", {})
        records: List[Dict[str, Any]] = []
        repair_trace: List[Dict[str, Any]] = []
        last_assessment = InsufficiencyAssessment(
            sufficient=False,
            reason="no_attempts",
            missing_slots=tuple(intent.get("focus_slots", [])),
            row_count=0,
        )
        last_bundle = semantic_context.get("evidence_bundle_preview", {})

        for strategy in self._strategy_sequence(attempt_limit):
            plan = self._build_plan(
                question=question,
                semantic_context=semantic_context,
                anchor_match=anchor_match,
                intent=intent,
                constraint_slice=constraint_slice,
                strategy=strategy,
            )
            validation = self.validator.validate(plan, constraint_slice)
            if not validation["ok"]:
                repair_trace.append(
                    {
                        "strategy": strategy,
                        "database": plan.database,
                        "anchor_entity": plan.anchor_entity,
                        "status": "invalid",
                        "violations": validation["violations"],
                    }
                )
                last_assessment = InsufficiencyAssessment(
                    sufficient=False,
                    reason="validation_failed",
                    missing_slots=tuple(intent.get("focus_slots", [])),
                    row_count=0,
                )
                continue

            raw = self.connector.run_cypher(
                query=plan.query,
                database=plan.database,
                params=plan.params,
            )
            rows = _parse_cypher_rows(raw)
            annotated_rows = [
                {
                    "database": plan.database,
                    "graph_id": constraint_slice.get("graph_id"),
                    "strategy": strategy,
                    **row,
                }
                for row in rows
                if isinstance(row, dict)
            ]
            assessment = self.classifier.assess(intent, annotated_rows)
            evidence_bundle = self._build_runtime_evidence_bundle(
                question=question,
                semantic_context=semantic_context,
                intent=intent,
                rows=annotated_rows,
                plan=plan,
                assessment=assessment,
                constraint_slice=constraint_slice,
            )
            support_assessment = self.support_validator.finalize_runtime_support(
                preflight=anchor_match.get("support_assessment"),
                intent=intent,
                bundle=evidence_bundle,
                assessment=assessment,
                plan=plan,
                constraint_slice=constraint_slice,
            )
            evidence_bundle["support_assessment"] = support_assessment
            repair_trace.append(
                {
                    "strategy": strategy,
                    "database": plan.database,
                    "graph_id": constraint_slice.get("graph_id"),
                    "anchor_entity": plan.anchor_entity,
                    "relation_types": list(plan.relation_types),
                    "row_count": assessment.row_count,
                    "sufficient": assessment.sufficient,
                    "reason": assessment.reason,
                    "missing_slots": list(assessment.missing_slots),
                    "support_status": support_assessment.get("status"),
                    "coverage": support_assessment.get("coverage"),
                    "validation": {
                        "labels": validation["labels"],
                        "relation_types": validation["relation_types"],
                    },
                }
            )
            if annotated_rows:
                records = annotated_rows
            last_assessment = assessment
            last_bundle = evidence_bundle
            if assessment.sufficient:
                break

        return {
            "records": records,
            "repair_trace": repair_trace,
            "assessment": last_assessment,
            "evidence_bundle": last_bundle,
            "support_assessment": last_bundle.get("support_assessment", anchor_match.get("support_assessment", {})),
        }

    @staticmethod
    def _strategy_sequence(attempt_limit: int) -> List[str]:
        ordered = ["strict", "ontology_relaxed", "graph_broad"]
        return ordered[: max(1, attempt_limit)]

    def _build_plan(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
    ) -> CypherPlan:
        database = str(anchor_match.get("database", "")).strip()
        node_id = anchor_match.get("node_id")
        anchor_entity = str(anchor_match.get("display_name") or anchor_match.get("question_entity") or "").strip()
        anchor_label = self._pick_anchor_label(anchor_match, constraint_slice, strategy)
        target_hint = self._secondary_entity_hint(semantic_context, anchor_match) if strategy == "strict" else ""
        intent_id = str(intent.get("intent_id", "")).strip()
        profile_package = (
            select_semantic_profile_package(intent_id=intent_id, constraint_slice=constraint_slice)
            if strategy != "graph_broad"
            else None
        )
        relation_types = self._resolve_relation_types(
            question,
            intent,
            constraint_slice,
            strategy,
            profile_package=profile_package,
        )

        if intent_id == "relationship_lookup":
            query = self._relationship_query(anchor_label=anchor_label, relation_types=relation_types)
            params = {
                "node_id": node_id,
                "limit": self.result_limit,
                "target_hint": target_hint,
            }
        elif intent_id == "responsibility_lookup":
            query = self._responsibility_query(anchor_label=anchor_label, relation_types=relation_types)
            params = {"node_id": node_id, "limit": self.result_limit}
        else:
            query = self._entity_summary_query(anchor_label=anchor_label)
            params = {"node_id": node_id, "limit": self.result_limit}

        return CypherPlan(
            database=database,
            query=query,
            params=params,
            strategy=strategy,
            anchor_entity=anchor_entity,
            anchor_label=anchor_label,
            relation_types=tuple(relation_types),
            profile_id=profile_package.profile_id if profile_package else "",
            query_kind=profile_package.query_kind if profile_package else intent_id,
        )

    def _resolve_relation_types(
        self,
        question: str,
        intent: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
        profile_package: Optional[SemanticProfilePackage] = None,
    ) -> List[str]:
        allowed_relationship_types = [
            str(item).strip()
            for item in constraint_slice.get("allowed_relationship_types", [])
            if str(item).strip()
        ]
        allowed_lookup = {_normalize_symbol(item): item for item in allowed_relationship_types}
        alias_lookup = {
            _normalize_symbol(alias): relation_type
            for alias, relation_type in constraint_slice.get("relation_aliases", {}).items()
        }
        normalized_question = _normalize_symbol(question)
        matched_relations = [
            relation_type
            for alias, relation_type in alias_lookup.items()
            if alias and alias in normalized_question
        ]
        required_relations = [
            str(item).strip()
            for item in intent.get("required_relations", [])
            if str(item).strip()
        ]

        if strategy == "graph_broad":
            return []

        candidates = matched_relations + required_relations
        normalized_candidates = {_normalize_symbol(item): item for item in candidates if item}
        if strategy == "ontology_relaxed" and allowed_relationship_types:
            return allowed_relationship_types[:8]
        if not allowed_relationship_types:
            return list(dict.fromkeys(normalized_candidates.values()))

        constrained = [
            allowed_lookup[key]
            for key in normalized_candidates
            if key in allowed_lookup
        ]
        return apply_profile_relation_priority(
            package=profile_package,
            relation_types=list(dict.fromkeys(constrained)),
            constraint_slice=constraint_slice,
        )

    @staticmethod
    def _pick_anchor_label(
        anchor_match: Dict[str, Any],
        constraint_slice: Dict[str, Any],
        strategy: str,
    ) -> str:
        if strategy == "graph_broad":
            return ""
        allowed_labels = set(constraint_slice.get("allowed_labels", []))
        labels = [
            str(label).strip()
            for label in anchor_match.get("labels", [])
            if str(label).strip()
        ]
        if not labels:
            return ""
        if not allowed_labels:
            return labels[0]
        for label in labels:
            if label in allowed_labels:
                return label
        return ""

    @staticmethod
    def _secondary_entity_hint(
        semantic_context: Dict[str, Any],
        anchor_match: Dict[str, Any],
    ) -> str:
        anchor_name = str(anchor_match.get("display_name") or anchor_match.get("question_entity") or "").strip()
        for entity, candidates in semantic_context.get("matches", {}).items():
            if not candidates:
                continue
            display_name = str(candidates[0].get("display_name") or entity).strip()
            if display_name and display_name != anchor_name:
                return display_name
        return ""

    @staticmethod
    def _relationship_query(*, anchor_label: str, relation_types: Sequence[str]) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        relation_clause = ":" + "|".join(relation_types) if relation_types else ""
        return f"""
        MATCH (n{label_clause})
        WHERE elementId(n) = toString($node_id)
        OPTIONAL MATCH (n)-[r{relation_clause}]-(m)
        WHERE $target_hint = ''
           OR toLower(coalesce(m.name, m.title, m.id, m.uri, elementId(m))) CONTAINS toLower($target_hint)
        RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS source_entity,
               type(r) AS relation_type,
               coalesce(m.name, m.title, m.id, m.uri, elementId(m)) AS target_entity,
               labels(m) AS target_labels,
               coalesce(m.content_preview, m.description, '') AS supporting_fact
        ORDER BY target_entity
        LIMIT $limit
        """

    @staticmethod
    def _responsibility_query(*, anchor_label: str, relation_types: Sequence[str]) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        relation_clause = ":" + "|".join(relation_types) if relation_types else ""
        return f"""
        MATCH (target{label_clause})
        WHERE elementId(target) = toString($node_id)
        OPTIONAL MATCH (counterparty)-[r{relation_clause}]-(target)
        RETURN coalesce(counterparty.name, counterparty.title, counterparty.id, counterparty.uri, elementId(counterparty)) AS owner_or_operator,
               type(r) AS relation_type,
               coalesce(target.name, target.title, target.id, target.uri, elementId(target)) AS target_entity,
               labels(counterparty) AS owner_labels,
               labels(target) AS target_labels,
               coalesce(counterparty.content_preview, counterparty.description, '') AS supporting_fact
        ORDER BY owner_or_operator
        LIMIT $limit
        """

    @staticmethod
    def _entity_summary_query(*, anchor_label: str) -> str:
        label_clause = f":{anchor_label}" if anchor_label else ""
        return f"""
        MATCH (n{label_clause})
        WHERE elementId(n) = toString($node_id)
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS target_entity,
               properties(n) AS properties,
               collect(
                 DISTINCT {{
                   relation: type(r),
                   target: coalesce(m.name, m.title, m.id, m.uri, elementId(m)),
                   target_labels: labels(m)
                 }}
               )[0..$limit] AS neighbors,
               coalesce(n.content_preview, n.description, n.content, '') AS supporting_fact
        LIMIT 1
        """

    def _build_runtime_evidence_bundle(
        self,
        *,
        question: str,
        semantic_context: Dict[str, Any],
        intent: Dict[str, Any],
        rows: Sequence[Dict[str, Any]],
        plan: CypherPlan,
        assessment: InsufficiencyAssessment,
        constraint_slice: Dict[str, Any],
    ) -> Dict[str, Any]:
        bundle = build_evidence_bundle(
            question=question,
            semantic_context=semantic_context,
            matched_entities=semantic_context.get("entities", []),
        )
        slot_fills = dict(bundle.get("slot_fills", {}))
        selected_triples: List[Dict[str, Any]] = []

        for row in rows[: self.result_limit]:
            relation_type = str(row.get("relation_type", "")).strip()
            if row.get("source_entity") and row.get("target_entity") and relation_type:
                slot_fills["source_entity"] = row.get("source_entity")
                slot_fills["target_entity"] = row.get("target_entity")
                slot_fills["relation_paths"] = [relation_type]
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                selected_triples.append(
                    {
                        "source": row.get("source_entity"),
                        "relation": relation_type,
                        "target": row.get("target_entity"),
                        "target_labels": row.get("target_labels", []),
                    }
                )
            if row.get("owner_or_operator"):
                slot_fills["owner_or_operator"] = row.get("owner_or_operator")
                slot_fills["target_entity"] = row.get("target_entity")
                if relation_type:
                    slot_fills["relation_paths"] = [relation_type]
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                selected_triples.append(
                    {
                        "source": row.get("owner_or_operator"),
                        "relation": relation_type or "RELATED_TO",
                        "target": row.get("target_entity"),
                        "target_labels": row.get("target_labels", []),
                    }
                )
            if row.get("target_entity") and (row.get("properties") or row.get("neighbors")):
                slot_fills["target_entity"] = row.get("target_entity")
                if row.get("supporting_fact"):
                    slot_fills["supporting_fact"] = row.get("supporting_fact")
                for neighbor in row.get("neighbors", [])[:5] if isinstance(row.get("neighbors"), list) else []:
                    if not isinstance(neighbor, dict):
                        continue
                    relation = str(neighbor.get("relation", "")).strip()
                    target = str(neighbor.get("target", "")).strip()
                    if relation and target:
                        selected_triples.append(
                            {
                                "source": row.get("target_entity"),
                                "relation": relation,
                                "target": target,
                                "target_labels": neighbor.get("target_labels", []),
                            }
                        )

        focus_slots = [str(slot).strip() for slot in intent.get("focus_slots", []) if str(slot).strip()]
        bundle["slot_fills"] = slot_fills
        bundle["selected_triples"] = selected_triples[:10]
        bundle["grounded_slots"] = [slot for slot in focus_slots if slot in slot_fills]
        bundle["missing_slots"] = [slot for slot in focus_slots if slot not in slot_fills]
        bundle["coverage"] = round(
            len(bundle["grounded_slots"]) / max(1, len(focus_slots)),
            4,
        ) if focus_slots else 1.0
        bundle["database"] = plan.database
        bundle["graph_id"] = constraint_slice.get("graph_id")
        if plan.profile_id:
            bundle["deterministic_profile"] = {
                "profile_id": plan.profile_id,
                "query_kind": plan.query_kind,
            }
        bundle["reasoning"] = {
            "strategy": plan.strategy,
            "anchor_entity": plan.anchor_entity,
            "anchor_label": plan.anchor_label,
            "relation_types": list(plan.relation_types),
            "profile_id": plan.profile_id,
            "query_kind": plan.query_kind,
            "assessment": {
                "sufficient": assessment.sufficient,
                "reason": assessment.reason,
                "missing_slots": list(assessment.missing_slots),
                "row_count": assessment.row_count,
            },
        }
        return bundle

    @staticmethod
    def _summarize_constraint_slice(constraint_slice: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(constraint_slice, dict):
            return {}
        return {
            "graph_id": constraint_slice.get("graph_id"),
            "database": constraint_slice.get("database"),
            "ontology_id": constraint_slice.get("ontology_id"),
            "constraint_strength": constraint_slice.get("constraint_strength"),
            "artifact_ids": list(constraint_slice.get("artifact_ids", [])),
            "label_count": len(constraint_slice.get("allowed_labels", [])),
            "relationship_count": len(constraint_slice.get("allowed_relationship_types", [])),
            "property_count": len(constraint_slice.get("allowed_properties", [])),
        }

    def _label_distribution(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class RDFAgent:
    """RDF-oriented query agent."""

    def __init__(self, connector: Any, result_limit: int = 20):
        self.connector = connector
        self.result_limit = result_limit

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        entities = semantic_context.get("entities", [])
        if entities:
            rows = self._resource_matches(databases, entities[0])
            if rows:
                return {
                    "mode": "rdf",
                    "summary": "Matched RDF-like resources using URI/name signals.",
                    "records": rows,
                }

        return {
            "mode": "rdf",
            "summary": "No RDF resource match found. Returned RDF label overview.",
            "records": self._rdf_label_overview(databases),
        }

    def _resource_matches(self, databases: Sequence[str], entity_text: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE (
            any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
            OR n.uri IS NOT NULL
        )
          AND any(key IN ['uri', 'name', 'title', 'id']
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN labels(n) AS labels,
               coalesce(n.uri, n.name, n.title, n.id, elementId(n)) AS resource,
               n.name AS name
        LIMIT $limit
        """
        all_rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(
                query=query,
                database=db_name,
                params={"query": entity_text, "limit": self.result_limit},
            )
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    all_rows.append({"database": db_name, **row})
        return all_rows

    def _rdf_label_overview(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
           OR n.uri IS NOT NULL
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class AnswerGenerationAgent:
    """Generate final response payload from routed agent outputs."""

    def synthesize(
        self,
        question: str,
        route: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
    ) -> str:
        entities = semantic_context.get("entities", [])
        unresolved = semantic_context.get("unresolved_entities", [])
        intent = semantic_context.get("intent", {})
        evidence_bundle = semantic_context.get("evidence_bundle_preview", {})
        support_assessment = semantic_context.get("support_assessment", {})
        strategy_decision = semantic_context.get("strategy_decision", {})

        lines = [f"Route selected: {route.upper()}."]
        if intent.get("intent_id"):
            lines.append(f"Intent: {intent['intent_id']}.")
        if entities:
            lines.append(f"Extracted entities: {', '.join(entities)}.")
        if unresolved:
            lines.append(f"Unresolved entities: {', '.join(unresolved)}.")
        if support_assessment.get("status"):
            lines.append(
                f"Support status: {support_assessment['status']} ({support_assessment.get('reason', 'unspecified')})."
            )
        if evidence_bundle.get("grounded_slots"):
            lines.append(f"Grounded slots: {', '.join(evidence_bundle['grounded_slots'])}.")
        if evidence_bundle.get("missing_slots"):
            lines.append(f"Missing slots: {', '.join(evidence_bundle['missing_slots'])}.")
        reasoning = semantic_context.get("reasoning", {})
        if reasoning.get("requested"):
            lines.append(
                f"Reasoning mode used {int(reasoning.get('attempt_count', 0))} retrieval attempt(s)."
            )
            if reasoning.get("terminal_reason"):
                lines.append(f"Reasoning terminal state: {reasoning['terminal_reason']}.")
        if strategy_decision.get("advanced_debate_recommended"):
            lines.append("Advanced debate is recommended if you want a cross-graph comparison.")
        elif strategy_decision.get("next_mode_hint") == "reasoning_mode":
            lines.append("A bounded repair retry is recommended for a stronger retrieval attempt.")

        if lpg_result and lpg_result.get("records"):
            lines.append(f"LPG records: {len(lpg_result['records'])}.")
        if rdf_result and rdf_result.get("records"):
            lines.append(f"RDF records: {len(rdf_result['records'])}.")
        if not ((lpg_result and lpg_result.get("records")) or (rdf_result and rdf_result.get("records"))):
            lines.append("No matching graph records were found for this question.")

        return " ".join(lines)


__all__ = [
    "SemanticEntityResolver",
    "QueryRouterAgent",
    "LPGAgent",
    "RDFAgent",
    "AnswerGenerationAgent",
]
