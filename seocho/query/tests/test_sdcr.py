from seocho.query.sdcr import ABSTAIN, CAPABILITY_FALLBACK, Capability, CapabilityRegistry, Evidence, SDCRRouter, detect_conflicts, filter_evidence, verify_conflicts


def test_uncoverable_request_abstains_instead_of_reporting_single_view() -> None:
    """A total coverage failure must not look like a chosen single view.

    Regression: the router previously returned reason="single_view" with an empty
    selection whenever nothing covered the request, so a caller reading the
    receipt would proceed as though one view had been selected.
    """
    receipt = SDCRRouter(fallback_team_size=0).route(
        workspace_id="w1",
        required_slots=["revenue_2024", "segment_mix"],
        capabilities=[Capability("financials", frozenset({"headcount"}))],
    )
    assert receipt.selected_views == ()
    assert receipt.reason == ABSTAIN
    assert receipt.abstained is True
    assert receipt.missing_slots == ("revenue_2024", "segment_mix")
    assert receipt.as_dict()["abstained"] is True


def test_capability_fallback_serves_a_team_instead_of_empty_evidence() -> None:
    """An uncovered request degrades to the capability team, and says so.

    The frozen replay (log2026-capability-fallback-v1) measured .000 slot token
    recall on routing misses against .154 for the capability team on the same
    cases, so serving nothing is never the right default.
    """
    router = SDCRRouter(fallback_team_size=2)
    receipt = router.route(
        workspace_id="w1",
        required_slots=["revenue_2024", "segment_mix"],
        capabilities=[
            Capability("financials", frozenset({"headcount"}), priority=3),
            Capability("footnotes", frozenset({"other"}), priority=2),
            Capability("legal", frozenset({"unrelated"}), priority=1),
        ],
    )
    assert receipt.reason == CAPABILITY_FALLBACK
    assert receipt.fallback_used is True
    assert receipt.selected_views == ("financials", "footnotes")
    assert receipt.abstained is False
    # The unmet slots stay visible; the fallback does not mask the gap.
    assert receipt.missing_slots == ("revenue_2024", "segment_mix")


def test_denied_views_are_reported_and_distinguished_from_absent_facts() -> None:
    """authorization_passed was vacuously true; denial is now observable.

    It previously applied all() to the already-authorized list, so it could never
    be False and carried no signal. The receipt now names the denied views and
    the slots only a denied view could have filled.
    """
    receipt = SDCRRouter(fallback_team_size=0).route(
        workspace_id="w1",
        required_slots=["revenue", "insider_comp"],
        capabilities=[
            Capability("financials", frozenset({"revenue"}), priority=2),
            Capability("governance", frozenset({"insider_comp"}), authorized=False),
        ],
    )
    assert receipt.selected_views == ("financials",)
    assert receipt.denied_views == ("governance",)
    assert receipt.authorization_blocked_slots == ("insider_comp",)
    # The selection itself never contains an unauthorized view.
    assert receipt.authorization_passed is True


def test_missing_fact_is_not_reported_as_an_authorization_block() -> None:
    receipt = SDCRRouter(fallback_team_size=0).route(
        workspace_id="w1",
        required_slots=["revenue", "nobody_has_this"],
        capabilities=[Capability("financials", frozenset({"revenue"}))],
    )
    assert receipt.missing_slots == ("nobody_has_this",)
    assert receipt.denied_views == ()
    assert receipt.authorization_blocked_slots == ()


def test_router_selects_smallest_authorized_coalition() -> None:
    receipt = SDCRRouter().route(
        workspace_id="w1",
        required_slots=["revenue", "legal_risk"],
        capabilities=[
            Capability("financials", frozenset({"revenue"}), priority=2),
            Capability("legal", frozenset({"legal_risk"}), priority=1),
            Capability("broadcast", frozenset({"revenue", "legal_risk"}), authorized=False),
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
    registry = CapabilityRegistry([Capability("legal", frozenset({"risk"}), priority=1)])
    registry.register(Capability("financials", frozenset({"revenue"}), priority=2))
    assert [item.view_id for item in registry.authorized("w1")] == ["legal", "financials"]
    assert registry.snapshot()[0]["view_id"] == "financials"
