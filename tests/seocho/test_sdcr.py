from seocho.query.sdcr import (
    Capability,
    CapabilityRegistry,
    Evidence,
    SDCRRouter,
    detect_conflicts,
    filter_evidence,
    verify_conflicts,
)


def test_router_selects_smallest_authorized_coalition() -> None:
    receipt = SDCRRouter().route(
        workspace_id="w1",
        required_slots=["revenue", "legal_risk"],
        capabilities=[
            Capability("financials", frozenset({"revenue"}), priority=2),
            Capability("legal", frozenset({"legal_risk"}), priority=1),
            Capability(
                "broadcast", frozenset({"revenue", "legal_risk"}), authorized=False
            ),
        ],
    )
    assert receipt.selected_views == ("financials", "legal")
    assert receipt.missing_slots == ()
    assert receipt.reason == "slot_gap"
    assert receipt.authorization_passed is True


def test_filter_and_conflict_detection_preserve_safe_evidence() -> None:
    evidence = [
        Evidence("a", "financials", "revenue", 10),
        Evidence("b", "legal", "revenue", 12),
        Evidence("secret", "legal", "legal_risk", "high", protected=True),
    ]
    safe = filter_evidence(evidence)
    assert [item.source_id for item in safe] == ["a", "b"]
    assert detect_conflicts(evidence) == ("revenue",)
    packet = verify_conflicts(evidence)
    assert packet["status"] == "conflict"
    assert packet["conflicts"] == ["revenue"]


def test_capability_registry_has_deterministic_snapshot() -> None:
    registry = CapabilityRegistry(
        [Capability("legal", frozenset({"risk"}), priority=1)]
    )
    registry.register(Capability("financials", frozenset({"revenue"}), priority=2))
    assert [item.view_id for item in registry.authorized("w1")] == [
        "legal",
        "financials",
    ]
    assert registry.snapshot()[0]["view_id"] == "financials"
