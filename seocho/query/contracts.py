from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class IntentSpec:
    """Intent contract used by semantic query planning and evidence shaping."""

    intent_id: str
    required_relations: Tuple[str, ...]
    required_entity_types: Tuple[str, ...]
    focus_slots: Tuple[str, ...]
    trigger_keywords: Tuple[str, ...]


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
