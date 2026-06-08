"""Ontology-aware agent capability matchmaking (Sycara / RETSINA / OWL-S).

SEOCHO routes work to agents with a hardcoded keyword router
(``QueryRouterAgent``: scan the question for RDF/LPG hint words). That can't
express *what an agent is good at* — only what words trigger it — and it can't
respect an agent's **ontology scope** (a finance specialist vs a generic graph
agent).

This module is the RETSINA pattern: agents **advertise capabilities**
(:class:`AgentCapability` — which task kinds they handle, which ontology concepts
they cover, what inputs they need), and a middle-agent :class:`Matchmaker`
**matches** an incoming :class:`TaskDescriptor` to the best-scoring capability.
It is a pure, deterministic decision (no LLM), so selection is debuggable and
unit-testable — the same role the model-tier router plays for *models*, this
plays for *agents*.

A capability is only eligible if it can actually run the task (it handles the
kind and its required inputs are available). Among eligible capabilities, the
score rewards **ontology-scope overlap** — a scoped specialist beats a generic
agent on its own concepts, while a generic agent (empty scope = "any") still
covers everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Set

__all__ = ["AgentCapability", "TaskDescriptor", "Match", "Matchmaker"]


def _fs(values) -> FrozenSet[str]:
    return frozenset(str(v).lower() for v in (values or ()))


@dataclass(frozen=True)
class AgentCapability:
    """An agent's advertised profile.

    ``ontology_scope`` empty means "any concept" (a generalist). ``inputs`` are
    the input kinds the agent needs to run; if the task can't supply them the
    agent is not eligible. ``priority`` breaks ties between equally-scored peers.
    """

    name: str
    handles: FrozenSet[str]                       # task kinds / intents served
    ontology_scope: FrozenSet[str] = frozenset()  # concepts covered; empty = any
    inputs: FrozenSet[str] = frozenset()          # required input kinds
    outputs: FrozenSet[str] = frozenset()
    priority: float = 0.0

    @classmethod
    def make(cls, name, handles, *, ontology_scope=(), inputs=(), outputs=(), priority=0.0):
        return cls(
            name=name,
            handles=_fs(handles),
            ontology_scope=_fs(ontology_scope),
            inputs=_fs(inputs),
            outputs=_fs(outputs),
            priority=priority,
        )


@dataclass(frozen=True)
class TaskDescriptor:
    kind: str
    concepts: FrozenSet[str] = frozenset()
    available_inputs: FrozenSet[str] = frozenset()

    @classmethod
    def make(cls, kind, *, concepts=(), available_inputs=()):
        return cls(kind=str(kind).lower(), concepts=_fs(concepts), available_inputs=_fs(available_inputs))


@dataclass(frozen=True)
class Match:
    capability: AgentCapability
    score: float
    reasons: List[str] = field(default_factory=list)


class Matchmaker:
    """Registry of advertised capabilities + best-match selection."""

    # scoring weights — scope overlap is what lets a specialist beat a generalist
    _KIND_BASE = 1.0
    _SCOPE_OVERLAP = 2.0      # per overlapping concept
    _GENERALIST_BONUS = 0.1   # tiny edge so a generalist still ranks above nothing

    def __init__(self) -> None:
        self._caps: List[AgentCapability] = []

    def advertise(self, capability: AgentCapability) -> None:
        self._caps.append(capability)

    def _eligible(self, cap: AgentCapability, task: TaskDescriptor) -> bool:
        # must handle the task kind AND have its required inputs available
        if task.kind not in cap.handles:
            return False
        return cap.inputs <= task.available_inputs

    def _score(self, cap: AgentCapability, task: TaskDescriptor) -> Match:
        reasons = [f"handles '{task.kind}'"]
        score = self._KIND_BASE
        if not cap.ontology_scope:
            score += self._GENERALIST_BONUS
            reasons.append("generalist (any concept)")
        else:
            overlap = cap.ontology_scope & task.concepts
            if overlap:
                score += self._SCOPE_OVERLAP * len(overlap)
                reasons.append(f"ontology-scope overlap {sorted(overlap)}")
            else:
                # scoped specialist but none of its concepts apply: still eligible
                # (it handles the kind) but unrewarded — a generalist will outrank it.
                reasons.append("scoped, no concept overlap")
        score += cap.priority
        if cap.priority:
            reasons.append(f"priority {cap.priority:+}")
        return Match(capability=cap, score=score, reasons=reasons)

    def rank(self, task: TaskDescriptor) -> List[Match]:
        """All eligible capabilities, highest score first (name as stable tiebreak)."""
        matches = [self._score(c, task) for c in self._caps if self._eligible(c, task)]
        return sorted(matches, key=lambda m: (-m.score, m.capability.name))

    def match(self, task: TaskDescriptor) -> Optional[Match]:
        """The single best capability for the task, or None if nothing can run it."""
        ranked = self.rank(task)
        return ranked[0] if ranked else None
