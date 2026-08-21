from __future__ import annotations

import pytest

from seocho.ontology.plane_policy import ProjectionPolicyError, decide_projection, projection_trace_receipt


def test_direct_and_shadow_cannot_make_a_canonical_claim() -> None:
    direct = decide_projection("direct", rust_socket=None, semantic_receipt=None, admission=None)
    shadow = decide_projection("shadow", rust_socket=None, semantic_receipt=None, admission=None)
    assert direct.allowed and shadow.allowed
    assert not direct.canonical_claim_allowed
    assert not shadow.canonical_claim_allowed
    assert shadow.missing == ("rust_projector_socket", "semantic_receipt", "lifecycle_admission")


@pytest.mark.parametrize("mode", ["governed", "lockdown"])
def test_strict_modes_fail_closed_without_complete_capability(mode: str) -> None:
    with pytest.raises(ProjectionPolicyError, match="rust_projector_socket"):
        decide_projection(mode, rust_socket=None, semantic_receipt=None, admission=None)


def test_governed_mode_requires_and_accepts_full_capability() -> None:
    decision = decide_projection(
        "governed",
        rust_socket="/tmp/seochod.sock",
        semantic_receipt={"rdf_bundle_sha256": "a" * 64},
        admission={"lease_id": "lease"},
    )
    assert decision.governance_enforced
    assert decision.canonical_claim_allowed
    assert decision.missing == ()


def test_lockdown_refuses_non_lpg_even_with_a_capability() -> None:
    with pytest.raises(ProjectionPolicyError, match="approved_lpg_projection"):
        decide_projection(
            "lockdown",
            rust_socket="/tmp/seochod.sock",
            semantic_receipt={"rdf_bundle_sha256": "a" * 64},
            admission={"lease_id": "lease"},
            graph_model="rdf",
        )


def test_trace_receipt_contains_only_stable_governance_identities() -> None:
    decision = decide_projection("governed", rust_socket="/tmp/sock", semantic_receipt={"x": 1}, admission={"lease_id": "lease", "epoch": 2})
    receipt = projection_trace_receipt(
        decision,
        semantic_receipt={"rdf_bundle_sha256": "b" * 64, "agent_profile_sha256": "p" * 64},
        admission={"lease_id": "lease", "epoch": 2},
    )
    assert receipt["canonical_claim_allowed"]
    assert receipt["rdf_bundle_sha256"] == "b" * 64
    assert receipt["lease_id"] == "lease"
    assert "socket" not in receipt
