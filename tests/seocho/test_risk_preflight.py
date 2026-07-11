from seocho.risk.preflight import (
    RiskPolicy,
    RiskSignalEvidence,
    SubjectDisclosureBinding,
    default_disclosure_policy,
    evaluate_risk_preflight,
)


POLICY = RiskPolicy(policy_id="wallet-risk", version="3.1.0")


def test_critical_signal_within_two_hops_blocks_by_policy() -> None:
    result = evaluate_risk_preflight(
        signals=(
            RiskSignalEvidence(
                reason_code="SANCTIONED_COUNTERPARTY",
                severity="critical",
                graph_hops=2,
                provenance_id="evidence-1",
            ),
        ),
        repeated_flagged_counterparties=0,
        policy=POLICY,
        projection_current=True,
    )
    assert result.disposition == "policy_block"
    assert result.reason_codes == ("critical_wallet_proximity",)
    assert result.authorizes_transaction is False


def test_stale_projection_fails_closed_to_review() -> None:
    result = evaluate_risk_preflight(
        signals=(),
        repeated_flagged_counterparties=0,
        policy=POLICY,
        projection_current=False,
    )
    assert result.disposition == "review_required"
    assert result.reason_codes == ("projection_not_current",)


def test_repeated_flagged_counterparties_are_policy_input_not_etcd_data() -> None:
    result = evaluate_risk_preflight(
        signals=(),
        repeated_flagged_counterparties=3,
        policy=POLICY,
        projection_current=True,
    )
    assert result.disposition == "review_required"
    assert result.reason_codes == ("repeated_flagged_counterparties",)


def test_out_of_bound_signal_does_not_create_a_clearance_decision() -> None:
    result = evaluate_risk_preflight(
        signals=(
            RiskSignalEvidence(
                reason_code="DISTANT_FLAG",
                severity="critical",
                graph_hops=8,
                provenance_id="evidence-8",
            ),
        ),
        repeated_flagged_counterparties=0,
        policy=POLICY,
        projection_current=True,
    )
    assert result.disposition == "continue_policy_evaluation"
    assert result.authorizes_transaction is False


def test_customer_output_removes_sensitive_ontology_properties() -> None:
    filtered = default_disclosure_policy().filter_record(
        {
            "disposition": "review_required",
            "reason_codes": ["high_risk_wallet_proximity"],
            "policy_version": "3.1.0",
            "graph_hops": 2,
            "provenance_id": "source-1",
            "customer_id": "alice",
            "wallet_hash": "hash-secret",
            "internal_risk_score": 0.98,
            "raw_wallet_address": "0xsecret",
        },
        role="customer",
    )
    assert filtered.visible == {
        "disposition": "review_required",
        "reason_codes": ["high_risk_wallet_proximity"],
        "policy_version": "3.1.0",
    }
    assert "customer_id" in filtered.redacted_fields
    assert "raw_wallet_address" in filtered.redacted_fields


def test_subject_specific_denial_is_applied_above_role_clearance() -> None:
    policy = default_disclosure_policy()
    filtered = policy.filter_for_subject(
        {"graph_hops": 2, "provenance_id": "source-1"},
        binding=SubjectDisclosureBinding(
            subject_ref_hash="sha256:subject",
            role="support",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            denied_fields=("provenance_id",),
        ),
    )
    assert filtered.visible == {"graph_hops": 2}
    assert filtered.redacted_fields == ("provenance_id",)


def test_stale_subject_policy_binding_is_rejected() -> None:
    policy = default_disclosure_policy()
    try:
        policy.filter_for_subject(
            {"disposition": "review_required"},
            binding=SubjectDisclosureBinding(
                subject_ref_hash="sha256:subject",
                role="customer",
                policy_id=policy.policy_id,
                policy_version="0.9.0",
            ),
        )
    except ValueError as exc:
        assert "does not match active policy" in str(exc)
    else:
        raise AssertionError("stale subject policy binding must fail")
