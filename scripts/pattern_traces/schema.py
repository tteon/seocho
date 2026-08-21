"""Trace schema v1 — one record per episode, across the six workload patterns.

This is the contract fix.10 flagged as the critical open item: it is both the
production trace log and the WP0 simulator input, so changing it later
invalidates every trace collected before the change. Hence the explicit
``trace_schema`` version on every record and a validator that refuses
unknown patterns rather than storing them.

The six patterns are the axes hadry named for the reasoning-model study
(2026-08-15): how models like MiniMax-M2.7 change behavior across

    indexing      extraction into the ontology (structured output)
    search        answer synthesis over retrieved graph context
    text2cypher   constrained query generation
    single_agent  one agent deciding its next tool action
    multi_agent   supervisor routing between specialists
    agent_agent   debate/critique between peers

An episode holds ordered steps: LLM calls (token usage verbatim from the
provider — reasoning-token accounting differs per provider and inventing a
normalization now would bake in one vendor's shape) and tool calls (rows,
db hits, node ids — the fields the cache-layer simulator consumes).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

TRACE_SCHEMA_VERSION = 1

PATTERNS = frozenset(
    {"indexing", "search", "text2cypher", "single_agent", "multi_agent", "agent_agent"}
)

# Parse outcomes for structured-output steps. ``salvaged`` means strict JSON
# failed but the balanced-object parser recovered — the event the
# SeochoLLMRepairRegression alert watches in production.
PARSE_OUTCOMES = frozenset({"ok", "salvaged", "failed", "not_applicable"})


@dataclass(slots=True)
class LLMStep:
    kind: str = field(default="llm", init=False)
    role: str = ""                      # e.g. "extract", "synthesize", "route"
    model: str = ""
    latency_ms: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)   # provider-verbatim
    text_chars: int = 0
    parse: str = "not_applicable"
    prompt_sections: Dict[str, int] = field(default_factory=dict)  # section -> chars
    error: Optional[str] = None


@dataclass(slots=True)
class ToolStep:
    kind: str = field(default="tool", init=False)
    name: str = ""
    latency_ms: float = 0.0
    rows: int = 0
    db_hits: int = 0
    node_ids: List[str] = field(default_factory=list)
    labels: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass(slots=True)
class Episode:
    pattern: str
    case_id: str
    model: str
    provider: str = "mara"
    trace_schema: int = field(default=TRACE_SCHEMA_VERSION, init=False)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    workspace_id: str = ""
    ontology: str = ""
    steps: List[Any] = field(default_factory=list)
    outcome: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.pattern not in PATTERNS:
            raise ValueError(
                f"unknown pattern {self.pattern!r}; v1 patterns are {sorted(PATTERNS)}"
            )
        for step in self.steps:
            parse = getattr(step, "parse", "not_applicable")
            if parse not in PARSE_OUTCOMES:
                raise ValueError(f"unknown parse outcome {parse!r}")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["steps"] = [asdict(step) for step in self.steps]
        return payload


def append_episode(path: Path, episode: Episode) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(episode.to_dict(), ensure_ascii=False, default=str) + "\n")


def read_episodes(path: Path) -> List[Dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("trace_schema") != TRACE_SCHEMA_VERSION:
                raise ValueError(
                    f"trace_schema {record.get('trace_schema')} != {TRACE_SCHEMA_VERSION}; "
                    f"refusing to silently mix schema generations"
                )
            records.append(record)
    return records
