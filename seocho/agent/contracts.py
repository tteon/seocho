from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

AGENT_EXECUTION_MODES = ("pipeline", "agent", "supervisor")


def normalize_execution_mode(mode: str | None) -> str:
    """Normalize agent execution mode to the canonical runtime contract."""
    candidate = str(mode or "").strip().lower()
    if candidate in AGENT_EXECUTION_MODES:
        return candidate
    return "pipeline"


@dataclass
class EntityRecord:
    """A single entity extracted and written during a session."""

    label: str
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    source_id: str = ""
    database: str = ""


@dataclass
class RelationshipRecord:
    """A single relationship extracted and written during a session."""

    source: str
    relationship_type: str
    target: str
    properties: Dict[str, Any] = field(default_factory=dict)
    source_id: str = ""
