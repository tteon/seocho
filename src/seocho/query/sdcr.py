"""Deterministic, authorization-aware coalition routing primitives.

The module intentionally contains no LLM or database calls. It is suitable for
the runtime policy layer and can be composed with the existing semantic flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Capability:
    """A graph view's declared answer-slot capabilities."""

    view_id: str
    slots: frozenset[str]
    authorized: bool = True
    priority: int = 0


@dataclass(frozen=True)
class Evidence:
    """Typed evidence passed to synthesis after policy filtering."""

    source_id: str
    view_id: str
    slot: str
    value: Any
    protected: bool = False
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionReceipt:
    workspace_id: str
    required_slots: tuple[str, ...]
    selected_views: tuple[str, ...]
    reason: str
    authorization_passed: bool
    missing_slots: tuple[str, ...]
    conflicts: tuple[str, ...]
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "required_slots": list(self.required_slots),
            "selected_views": list(self.selected_views),
            "reason": self.reason,
            "authorization_passed": self.authorization_passed,
            "missing_slots": list(self.missing_slots),
            "conflicts": list(self.conflicts),
            "timestamp": self.timestamp,
        }


class SDCRRouter:
    """Select the smallest authorized coalition that fills required slots."""

    def route(
        self,
        *,
        workspace_id: str,
        required_slots: Iterable[str],
        capabilities: Iterable[Capability],
        conflicts: Iterable[str] = (),
    ) -> DecisionReceipt:
        slots = tuple(dict.fromkeys(str(slot) for slot in required_slots if str(slot)))
        eligible = sorted(
            (cap for cap in capabilities if cap.authorized),
            key=lambda cap: (-cap.priority, cap.view_id),
        )
        covered: set[str] = set()
        selected: list[str] = []
        for capability in eligible:
            gain = set(slots).intersection(capability.slots) - covered
            if not gain:
                continue
            selected.append(capability.view_id)
            covered.update(gain)
            if covered.issuperset(slots):
                break
        missing = tuple(slot for slot in slots if slot not in covered)
        conflict_tuple = tuple(dict.fromkeys(str(item) for item in conflicts))
        reason = (
            "single_view"
            if len(selected) <= 1 and not conflict_tuple
            else ("conflict_verification" if conflict_tuple else "slot_gap")
        )
        return DecisionReceipt(
            workspace_id=workspace_id,
            required_slots=slots,
            selected_views=tuple(selected),
            reason=reason,
            authorization_passed=all(cap.authorized for cap in eligible),
            missing_slots=missing,
            conflicts=conflict_tuple,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


class CapabilityRegistry:
    """Workspace-local registry for authorized graph-view capabilities."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._items = {item.view_id: item for item in capabilities}

    def register(self, capability: Capability) -> None:
        self._items[capability.view_id] = capability

    def authorized(self, workspace_id: str) -> list[Capability]:
        del workspace_id  # authorization is applied by the caller's policy layer
        return [item for item in self._items.values() if item.authorized]

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "view_id": item.view_id,
                "slots": sorted(item.slots),
                "authorized": item.authorized,
                "priority": item.priority,
            }
            for item in sorted(self._items.values(), key=lambda value: value.view_id)
        ]


def filter_evidence(evidence: Iterable[Evidence]) -> list[Evidence]:
    """Remove protected evidence while retaining source traceability."""

    return [item for item in evidence if not item.protected]


def detect_conflicts(evidence: Iterable[Evidence]) -> tuple[str, ...]:
    """Return slots with incompatible values across views."""

    values: dict[str, set[str]] = {}
    for item in evidence:
        if item.protected:
            continue
        values.setdefault(item.slot, set()).add(repr(item.value))
    return tuple(
        sorted(slot for slot, slot_values in values.items() if len(slot_values) > 1)
    )


def verify_conflicts(evidence: Iterable[Evidence]) -> dict[str, Any]:
    """Return a typed reconciliation packet for the supervisor."""

    safe = filter_evidence(evidence)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in safe:
        grouped.setdefault(item.slot, []).append(
            {
                "source_id": item.source_id,
                "view_id": item.view_id,
                "value": item.value,
                "provenance": dict(item.provenance),
            }
        )
    conflicts = tuple(
        slot
        for slot, values in grouped.items()
        if len({repr(item["value"]) for item in values}) > 1
    )
    return {
        "status": "conflict" if conflicts else "consistent",
        "conflicts": list(conflicts),
        "candidates": grouped,
    }
