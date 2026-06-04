from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .contracts import EntityRecord, RelationshipRecord

logger = logging.getLogger(__name__)


# seocho-9gdm (depends-on seocho-vncn): query cache key shape and bounds.
# - Key tuple: (workspace_id, database, ontology_identity_hash, normalized_question)
# - LRU bound: cap entries to QUERY_CACHE_MAX_ENTRIES
# - TTL: each entry stamped with a monotonic timestamp; reads beyond
#   QUERY_CACHE_TTL_SECONDS miss the cache.
QUERY_CACHE_MAX_ENTRIES = 256
QUERY_CACHE_TTL_SECONDS = 3600.0  # 1 hour


def _normalize_query_for_cache(question: str) -> str:
    return question.strip().lower()


def _compose_query_cache_key(
    question: str,
    *,
    workspace_id: str = "",
    database: str = "",
    ontology_identity_hash: str = "",
    graph_epoch: str = "",
) -> Tuple[str, str, str, str, str]:
    return (
        str(workspace_id or ""),
        str(database or ""),
        str(ontology_identity_hash or ""),
        str(graph_epoch or ""),
        _normalize_query_for_cache(question),
    )


@dataclass
class SessionContext:
    """Structured context cache for an agent-level session."""

    indexed_sources: List[Dict[str, Any]] = field(default_factory=list)
    queries: List[Dict[str, Any]] = field(default_factory=list)
    total_nodes: int = 0
    total_relationships: int = 0
    entities: Dict[str, EntityRecord] = field(default_factory=dict)
    relationships: List[RelationshipRecord] = field(default_factory=list)
    # Composite cache: key tuple → (answer, monotonic_ts). OrderedDict gives
    # us O(1) move-to-end for LRU semantics.
    _query_cache: "OrderedDict[Tuple[str, str, str, str, str], Tuple[str, float]]" = field(
        default_factory=OrderedDict
    )
    _query_cache_max_entries: int = QUERY_CACHE_MAX_ENTRIES
    _query_cache_ttl_seconds: float = QUERY_CACHE_TTL_SECONDS
    # F2 (ADR-0102 follow-up): optional persistent L2 response cache. The
    # in-memory _query_cache above is L1 (dies with the Session); when this is
    # set (opt-in, default None) answers also persist across processes/restarts.
    # Same key shape incl. graph_epoch so a graph change invalidates lazily.
    response_cache: Optional[Any] = None

    def register_entities(self, nodes: List[Dict[str, Any]], source_id: str = "", database: str = "") -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            label = node.get("label", "")
            props = node.get("properties", {})
            name = props.get("name", node.get("id", ""))
            if not name:
                continue
            key = f"{label}::{name}".lower()
            self.entities[key] = EntityRecord(
                label=label,
                name=str(name),
                properties=props,
                source_id=source_id,
                database=database,
            )

    def register_relationships(self, rels: List[Dict[str, Any]], source_id: str = "") -> None:
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            self.relationships.append(
                RelationshipRecord(
                    source=str(rel.get("source", "")),
                    relationship_type=str(rel.get("type", rel.get("relationship_type", ""))),
                    target=str(rel.get("target", "")),
                    properties=rel.get("properties", {}),
                    source_id=source_id,
                )
            )

    def cache_query(
        self,
        question: str,
        answer: str,
        *,
        workspace_id: str = "",
        database: str = "",
        ontology_identity_hash: str = "",
        graph_epoch: str = "",
    ) -> None:
        """Cache an answer keyed by (workspace, database, ontology_hash,
        graph_epoch, question) in L1 (in-memory) and, if a persistent
        ``response_cache`` is configured, also in L2.

        seocho-9gdm: identity fields default to empty strings so legacy
        callers (no identity wiring) still get a working cache scoped to
        their Session — but two workspaces or two ontology versions in
        the same Session no longer collide. graph_epoch (F2) invalidates
        on graph mutation.
        """
        key = _compose_query_cache_key(
            question,
            workspace_id=workspace_id,
            database=database,
            ontology_identity_hash=ontology_identity_hash,
            graph_epoch=graph_epoch,
        )
        # L1: move-to-end then prune (classic LRU).
        self._query_cache[key] = (answer, time.monotonic())
        self._query_cache.move_to_end(key)
        while len(self._query_cache) > self._query_cache_max_entries:
            self._query_cache.popitem(last=False)
        # L2: persistent, cross-process. Never let a cache write break a query.
        if self.response_cache is not None:
            try:
                from ..response_cache import make_response_cache_key

                self.response_cache.put(
                    make_response_cache_key(
                        question, workspace_id=workspace_id, database=database,
                        ontology_identity_hash=ontology_identity_hash, graph_epoch=graph_epoch,
                    ),
                    answer,
                )
            except Exception:
                logger.debug("persistent response_cache put failed", exc_info=True)

    def get_cached_answer(
        self,
        question: str,
        *,
        workspace_id: str = "",
        database: str = "",
        ontology_identity_hash: str = "",
        graph_epoch: str = "",
    ) -> Optional[str]:
        """Look up a cached answer (L1 then optional persistent L2).

        Honours TTL + bumps LRU recency on an L1 hit. An L2 hit is promoted
        into L1 so subsequent same-session lookups stay hot.
        """
        key = _compose_query_cache_key(
            question,
            workspace_id=workspace_id,
            database=database,
            ontology_identity_hash=ontology_identity_hash,
            graph_epoch=graph_epoch,
        )
        record = self._query_cache.get(key)
        if record is not None:
            answer, ts = record
            if (time.monotonic() - ts) > self._query_cache_ttl_seconds:
                self._query_cache.pop(key, None)
            else:
                self._query_cache.move_to_end(key)
                return answer
        # L2: persistent lookup on L1 miss/expiry.
        if self.response_cache is not None:
            try:
                from ..response_cache import make_response_cache_key

                hit = self.response_cache.get(
                    make_response_cache_key(
                        question, workspace_id=workspace_id, database=database,
                        ontology_identity_hash=ontology_identity_hash, graph_epoch=graph_epoch,
                    )
                )
            except Exception:
                logger.debug("persistent response_cache get failed", exc_info=True)
                hit = None
            if hit is not None:
                # promote into L1
                self._query_cache[key] = (hit.answer, time.monotonic())
                self._query_cache.move_to_end(key)
                return hit.answer
        return None

    def add_indexing(
        self,
        source_id: str,
        nodes: int,
        rels: int,
        text_preview: str,
        *,
        mode: str = "agent",
        degraded: bool = False,
        fallback_from: str = "",
        fallback_reason: str = "",
    ) -> None:
        self.indexed_sources.append(
            {
                "source_id": source_id,
                "nodes": nodes,
                "relationships": rels,
                "text_preview": text_preview[:200],
                "mode": mode,
                "degraded": degraded,
                "fallback_from": fallback_from,
                "fallback_reason": fallback_reason,
            }
        )
        self.total_nodes += nodes
        self.total_relationships += rels

    def add_query(
        self,
        question: str,
        answer: str,
        cypher: str = "",
        *,
        mode: str = "agent",
        degraded: bool = False,
        fallback_from: str = "",
        fallback_reason: str = "",
    ) -> None:
        self.queries.append(
            {
                "question": question,
                "answer_preview": answer[:300],
                "cypher": cypher,
                "mode": mode,
                "degraded": degraded,
                "fallback_from": fallback_from,
                "fallback_reason": fallback_reason,
            }
        )

    def summary(self) -> str:
        parts = [
            f"Session indexed {len(self.indexed_sources)} document(s): "
            f"{self.total_nodes} nodes, {self.total_relationships} relationships."
        ]
        if self.entities:
            entity_names = [entity.name for entity in list(self.entities.values())[:10]]
            parts.append(f"Key entities: {', '.join(entity_names)}")
        if self.queries:
            parts.append(f"Answered {len(self.queries)} question(s).")
        return " ".join(parts)

    def to_agent_context(self, max_entities: int = 30, ontology: Any = None) -> str:
        lines: List[str] = []

        if self.entities:
            lines.append("=== Known Entities ===")
            for i, (_, entity) in enumerate(self.entities.items()):
                if i >= max_entities:
                    lines.append(f"  ... and {len(self.entities) - max_entities} more")
                    break
                props_str = ", ".join(f"{k}={v}" for k, v in list(entity.properties.items())[:5])
                lines.append(f"  [{entity.label}] {entity.name} ({props_str})")

        if self.relationships:
            lines.append("=== Known Relationships ===")
            for rel in self.relationships[:30]:
                lines.append(f"  {rel.source} -[{rel.relationship_type}]-> {rel.target}")

        if ontology is not None:
            try:
                plan = ontology.denormalization_plan()
                if plan:
                    lines.append("=== Denormalization Hints ===")
                    for label, info in plan.items():
                        for embed in info.get("embeds", []):
                            status = "SAFE (can query directly)" if embed.get("safe") else "BLOCKED (must traverse)"
                            fields = embed.get("fields", {})
                            field_str = ", ".join(f"{k}={v}" for k, v in list(fields.items())[:5])
                            lines.append(
                                f"  [{label}] via {embed.get('via', '?')} -> {embed.get('target', '?')}: "
                                f"{status}" + (f" — embedded fields: {field_str}" if field_str else "")
                            )
            except Exception:
                pass

        if self.queries:
            lines.append(f"=== Previous Queries ({len(self.queries)}) ===")
            for query in self.queries[-5:]:
                lines.append(f"  Q: {query['question'][:100]}")

        return "\n".join(lines) if lines else ""

