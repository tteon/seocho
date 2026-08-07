"""Production-oriented retrieval harness primitives.

The harness is deterministic and provider-neutral. It supplies identity,
bounded retrieval iteration, evidence-boundary checks, abstention, and rubric
evaluation without mutating policies automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from seocho.query.sdcr import Evidence, filter_evidence


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    workspace_id: str
    view_id: str
    allowed_slots: frozenset[str]


@dataclass(frozen=True)
class InsufficientEvidence:
    required_slots: tuple[str, ...]
    missing_slots: tuple[str, ...]
    attempts: int
    reason: str = "insufficient_evidence"

    def as_dict(self) -> dict[str, Any]:
        return {"status": "insufficient_evidence", "required_slots": list(self.required_slots), "missing_slots": list(self.missing_slots), "attempts": self.attempts, "reason": self.reason}


class EvidenceBoundary:
    """Reject evidence outside an agent identity's view and slot scope."""

    @staticmethod
    def authorize(identity: AgentIdentity, evidence: Iterable[Evidence]) -> list[Evidence]:
        safe = filter_evidence(evidence)
        return [item for item in safe if item.view_id == identity.view_id and item.slot in identity.allowed_slots]


class RetrievalSubagent:
    """Bounded retrieval loop that retries only when required slots are absent."""

    def __init__(self, *, max_attempts: int = 2) -> None:
        self.max_attempts = max(1, max_attempts)

    def run(
        self,
        *,
        required_slots: Iterable[str],
        retrieve: Callable[[int, tuple[str, ...]], Iterable[Evidence]],
    ) -> dict[str, Any]:
        required = tuple(dict.fromkeys(str(slot) for slot in required_slots if str(slot)))
        collected: list[Evidence] = []
        for attempt in range(1, self.max_attempts + 1):
            collected.extend(retrieve(attempt, required))
            covered = {item.slot for item in filter_evidence(collected)}
            missing = tuple(slot for slot in required if slot not in covered)
            if not missing:
                return {"status": "sufficient", "attempts": attempt, "evidence": filter_evidence(collected), "missing_slots": []}
        return {"status": "insufficient_evidence", "attempts": self.max_attempts, "evidence": filter_evidence(collected), "missing_slots": list(missing), "abstention": InsufficientEvidence(required, missing, self.max_attempts).as_dict()}


def evaluate_rubric(*, receipt: Mapping[str, Any], evidence: Iterable[Evidence], answer: str = "") -> dict[str, Any]:
    """Evaluate production-safe behavior without judging answer semantics."""

    safe = list(filter_evidence(evidence))
    return {
        "slot_coverage": 1.0 - len(receipt.get("missing_slots", [])) / max(1, len(receipt.get("required_slots", []))),
        "authorization_passed": bool(receipt.get("authorization_passed", False)),
        "protected_evidence_removed": len(list(evidence)) >= len(safe),
        "source_trace_present": all(bool(item.source_id and item.provenance is not None) for item in safe),
        "answer_present": bool(answer.strip()),
    }
