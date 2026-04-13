from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import EntityRecord, RelationshipRecord


@dataclass
class SessionContext:
    """Structured context cache for an agent-level session."""

    indexed_sources: List[Dict[str, Any]] = field(default_factory=list)
    queries: List[Dict[str, Any]] = field(default_factory=list)
    total_nodes: int = 0
    total_relationships: int = 0
    entities: Dict[str, EntityRecord] = field(default_factory=dict)
    relationships: List[RelationshipRecord] = field(default_factory=list)
    _query_cache: Dict[str, str] = field(default_factory=dict)

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

    def cache_query(self, question: str, answer: str) -> None:
        self._query_cache[question.strip().lower()] = answer

    def get_cached_answer(self, question: str) -> Optional[str]:
        return self._query_cache.get(question.strip().lower())

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

