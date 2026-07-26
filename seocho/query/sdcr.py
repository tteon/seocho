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


#: Terminal routing actions. These mirror the four actions the routing rule can
#: take; ``abstain`` exists so a total coverage failure is never reported as if a
#: view had been chosen.
SINGLE_VIEW = "single_view"
SLOT_GAP = "slot_gap"
CONFLICT_VERIFICATION = "conflict_verification"
CAPABILITY_FALLBACK = "capability_fallback"
ABSTAIN = "abstain"


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
    denied_views: tuple[str, ...] = ()
    authorization_blocked_slots: tuple[str, ...] = ()
    fallback_used: bool = False

    @property
    def abstained(self) -> bool:
        return self.reason == ABSTAIN

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "required_slots": list(self.required_slots),
            "selected_views": list(self.selected_views),
            "reason": self.reason,
            "authorization_passed": self.authorization_passed,
            "denied_views": list(self.denied_views),
            "authorization_blocked_slots": list(self.authorization_blocked_slots),
            "missing_slots": list(self.missing_slots),
            "conflicts": list(self.conflicts),
            "fallback_used": self.fallback_used,
            "abstained": self.abstained,
            "timestamp": self.timestamp,
        }


class SDCRRouter:
    """Select the smallest authorized coalition that fills required slots.

    ``fallback_team_size`` controls what happens when slot coverage is
    incomplete. Serving nothing is the worst available outcome: in the frozen
    13-case replay a routing miss scored .000 slot token recall while the
    capability team scored .154 on the same cases, so an uncovered request
    degrades to the top-priority capability team rather than to empty evidence
    (``log2026-capability-fallback-v1``). Set it to 0 to disable the fallback and
    abstain instead.
    """

    def __init__(self, *, fallback_team_size: int = 2) -> None:
        if fallback_team_size < 0:
            raise ValueError("fallback_team_size must be non-negative")
        self._fallback_team_size = fallback_team_size

    def route(
        self,
        *,
        workspace_id: str,
        required_slots: Iterable[str],
        capabilities: Iterable[Capability],
        conflicts: Iterable[str] = (),
    ) -> DecisionReceipt:
        slots = tuple(dict.fromkeys(str(slot) for slot in required_slots if str(slot)))
        offered = list(capabilities)
        eligible = sorted(
            (cap for cap in offered if cap.authorized),
            key=lambda cap: (-cap.priority, cap.view_id),
        )
        denied = tuple(sorted(cap.view_id for cap in offered if not cap.authorized))

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

        fallback_used = False
        if missing and self._fallback_team_size:
            # Slot descriptors did not cover the request. Serve the highest-priority
            # capability team instead of empty evidence, and record that we did.
            team = [cap.view_id for cap in eligible[: self._fallback_team_size]]
            if team and team != selected:
                selected = list(dict.fromkeys(selected + team))
                fallback_used = True

        if not selected:
            reason = ABSTAIN
        elif conflict_tuple:
            reason = CONFLICT_VERIFICATION
        elif fallback_used:
            reason = CAPABILITY_FALLBACK
        elif missing:
            reason = SLOT_GAP
        elif len(selected) <= 1:
            reason = SINGLE_VIEW
        else:
            reason = SLOT_GAP

        # Slots that only a denied view could have filled. This is the actionable
        # distinction the receipt previously could not express: "no view holds this
        # fact" versus "you are not authorized to read the view that does".
        authorized_ids = {cap.view_id for cap in eligible}
        blocked = tuple(
            slot for slot in missing
            if any(slot in cap.slots for cap in offered if not cap.authorized)
        )
        return DecisionReceipt(
            workspace_id=workspace_id,
            required_slots=slots,
            selected_views=tuple(selected),
            reason=reason,
            # Output invariant: no unauthorized view may reach the selection. The
            # previous form applied all() to the already-filtered eligible list,
            # so it was vacuously true and could never report a failure.
            authorization_passed=all(view in authorized_ids for view in selected),
            missing_slots=missing,
            conflicts=conflict_tuple,
            timestamp=datetime.now(timezone.utc).isoformat(),
            denied_views=denied,
            authorization_blocked_slots=blocked,
            fallback_used=fallback_used,
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
            {"view_id": item.view_id, "slots": sorted(item.slots), "authorized": item.authorized, "priority": item.priority}
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
    return tuple(sorted(slot for slot, slot_values in values.items() if len(slot_values) > 1))


def verify_conflicts(evidence: Iterable[Evidence]) -> dict[str, Any]:
    """Return a typed reconciliation packet for the supervisor."""

    safe = filter_evidence(evidence)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in safe:
        grouped.setdefault(item.slot, []).append(
            {"source_id": item.source_id, "view_id": item.view_id, "value": item.value, "provenance": dict(item.provenance)}
        )
    conflicts = tuple(slot for slot, values in grouped.items() if len({repr(item["value"]) for item in values}) > 1)
    return {"status": "conflict" if conflicts else "consistent", "conflicts": list(conflicts), "candidates": grouped}
