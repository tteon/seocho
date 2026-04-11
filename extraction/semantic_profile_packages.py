from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


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
