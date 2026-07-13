from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from seocho.query.evidence_swarm import (
    EvidenceSwarmCoordinator,
    EvidenceSwarmRequest,
)
from seocho.query.sdcr import Capability, Evidence


@dataclass
class Specialist:
    capability: Capability
    values: tuple[Evidence, ...] = ()
    barrier: threading.Barrier | None = None
    error: Exception | None = None
    seen_workspace: str = ""

    def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
        self.seen_workspace = request.workspace_id
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        if self.error is not None:
            raise self.error
        return self.values


def _request(*slots: str) -> EvidenceSwarmRequest:
    return EvidenceSwarmRequest(
        workspace_id="tenant-a",
        intent_id="memory_compare",
        question="What changed?",
        required_slots=slots,
    )


def test_runs_smallest_authorized_coalition_concurrently() -> None:
    barrier = threading.Barrier(2)
    current = Specialist(
        Capability("current", frozenset({"current_state"}), priority=2),
        (
            Evidence(
                "rev-2",
                "current",
                "current_state",
                "confirmed",
                provenance={"sequence": 2},
            ),
        ),
        barrier=barrier,
    )
    historical = Specialist(
        Capability("historical", frozenset({"historical_state"}), priority=1),
        (
            Evidence(
                "rev-1",
                "historical",
                "historical_state",
                "pending",
                provenance={"sequence": 1},
            ),
        ),
        barrier=barrier,
    )
    unused = Specialist(
        Capability("unused", frozenset({"other"}), priority=99),
    )

    result = EvidenceSwarmCoordinator(
        [current, historical, unused], timeout_seconds=2
    ).run(_request("current_state", "historical_state"))

    assert result.status == "complete"
    assert result.receipt.selected_views == ("current", "historical")
    assert tuple(run.view_id for run in result.specialist_runs) == (
        "current",
        "historical",
    )
    assert current.seen_workspace == historical.seen_workspace == "tenant-a"
    assert unused.seen_workspace == ""
    assert result.bundle.missing_slots == ()


def test_filters_protected_evidence_and_surfaces_conflicts() -> None:
    left = Specialist(
        Capability("authority", frozenset({"state", "wallet"}), priority=2),
        (
            Evidence("r1", "authority", "state", "confirmed"),
            Evidence("r1-secret", "authority", "wallet", "raw-address", protected=True),
        ),
    )
    right = Specialist(
        Capability("projection", frozenset({"projection_check"}), priority=1),
        (
            Evidence("p1", "projection", "projection_check", "stale"),
            Evidence("p2", "projection", "projection_check", "current"),
        ),
    )

    result = EvidenceSwarmCoordinator([left, right]).run(
        _request("state", "projection_check")
    )

    assert result.status == "partial"
    assert result.bundle.protected_evidence_count == 1
    assert all(item.value != "raw-address" for item in result.bundle.selected_evidence)
    assert result.bundle.conflict_verification["status"] == "conflict"
    assert result.bundle.conflict_verification["conflicts"] == ["projection_check"]


def test_specialist_failure_keeps_missing_slot_visible() -> None:
    good = Specialist(
        Capability("current", frozenset({"current_state"}), priority=2),
        (Evidence("r1", "current", "current_state", "confirmed"),),
    )
    failed = Specialist(
        Capability("history", frozenset({"historical_state"}), priority=1),
        error=RuntimeError("database unavailable"),
    )

    result = EvidenceSwarmCoordinator([good, failed]).run(
        _request("current_state", "historical_state")
    )

    assert result.status == "partial"
    assert result.bundle.missing_slots == ("historical_state",)
    assert (
        next(
            run for run in result.specialist_runs if run.view_id == "history"
        ).error_type
        == "RuntimeError"
    )
    assert "database unavailable" not in str(result.as_dict())


def test_unauthorized_specialist_is_not_executed() -> None:
    denied = Specialist(
        Capability("private", frozenset({"state"}), authorized=False, priority=9),
        (Evidence("secret", "private", "state", "hidden"),),
    )

    result = EvidenceSwarmCoordinator([denied]).run(_request("state"))

    assert result.status == "partial"
    assert result.receipt.selected_views == ()
    assert result.bundle.missing_slots == ("state",)
    assert denied.seen_workspace == ""


def test_timeout_is_reported_without_blocking_for_slow_specialist() -> None:
    class SlowSpecialist(Specialist):
        def retrieve(self, request: EvidenceSwarmRequest) -> tuple[Evidence, ...]:
            time.sleep(0.2)
            return super().retrieve(request)

    slow = SlowSpecialist(
        Capability("slow", frozenset({"state"})),
        (Evidence("r1", "slow", "state", "confirmed"),),
    )
    started = time.perf_counter()

    result = EvidenceSwarmCoordinator([slow], timeout_seconds=0.02).run(
        _request("state")
    )

    assert time.perf_counter() - started < 0.15
    assert result.status == "partial"
    assert result.specialist_runs[0].outcome == "timeout"
    assert result.bundle.missing_slots == ("state",)
