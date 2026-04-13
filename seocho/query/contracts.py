from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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

