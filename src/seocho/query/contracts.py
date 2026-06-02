from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class IntentSpec:
    """Intent contract used by semantic query planning and evidence shaping."""

    intent_id: str
    required_relations: Tuple[str, ...]
    required_entity_types: Tuple[str, ...]
    focus_slots: Tuple[str, ...]
    trigger_keywords: Tuple[str, ...]


@dataclass(frozen=True)
class PatternSpec:
    """One Cypher generation pattern in the GOPTS catalog (ADR-0097).

    PatternSpec is the registry entry consumed by the cost-ranked plan
    enumerator (G2). G3 lifts the inline pattern branches out of
    CypherBuilder.build() and registers each one here, one PatternSpec
    per branch. G2 reads ``alternatives`` to widen enumeration when a
    pattern can substitute for a different cypher_shape (e.g. a
    shortest_path pattern can answer some relationship_lookup
    questions).

    Fields:
        pattern_id:           registry key, e.g. "pattern:relationship_lookup_hop1"
        intent_id:            link to IntentSpec.intent_id vocabulary (best-effort)
        cypher_shape:         link to CypherBuilder.build() dispatch string
        required_labels:      labels the pattern needs present in schema
        required_relations:   relationship types the pattern needs present
        schema_preconditions: opaque preconditions for the cost ranker (G2 uses)
        cost_hints:           cost-model hints (e.g. {"prefers_indexed": [...]})
        template_factory:     (builder, **build_kwargs) -> (cypher, params)
        alternatives:         cypher_shape strings this pattern can substitute
                              for during G2 enumeration. Empty for primary-only
                              patterns; non-empty when one pattern can answer
                              multiple shapes (G2 multi-candidate fan-out).
    """

    pattern_id: str
    intent_id: str
    cypher_shape: str
    required_labels: Tuple[str, ...]
    required_relations: Tuple[str, ...]
    schema_preconditions: Tuple[str, ...]
    cost_hints: Dict[str, Any]
    template_factory: Callable[..., Tuple[str, Dict[str, Any]]]
    alternatives: Tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryPlan:
    """Canonical query plan produced by the planner."""

    question: str
    cypher: str
    params: Dict[str, Any] = field(default_factory=dict)
    intent_data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.cypher) and not self.error


@dataclass(frozen=True)
class QueryExecution:
    """Canonical query execution result returned by the executor."""

    cypher: str
    params: Dict[str, Any] = field(default_factory=dict)
    records: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class QueryAttempt:
    """Compact record of one query attempt for repair-aware answering."""

    cypher: str
    result_count: int
    error: Optional[str] = None


@dataclass(frozen=True)
class CypherPlan:
    """Deterministic semantic query plan used by semantic graph answering."""

    database: str
    query: str
    params: Dict[str, Any]
    strategy: str
    anchor_entity: str
    anchor_label: str = ""
    relation_types: Tuple[str, ...] = ()
    profile_id: str = ""
    query_kind: str = ""


@dataclass(frozen=True)
class InsufficiencyAssessment:
    """Assessment of whether a graph retrieval filled the intended slots."""

    sufficient: bool
    reason: str
    missing_slots: Tuple[str, ...]
    row_count: int
    filled_slots: Tuple[str, ...] = ()
