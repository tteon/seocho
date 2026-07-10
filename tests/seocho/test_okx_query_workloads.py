from seocho.query.workloads import (
    WITHDRAWAL_EXPLANATION,
    classify_okx_query,
)


def test_withdrawal_query_classification_is_deterministic() -> None:
    assert classify_okx_query("Why can't I withdraw ETH?") is WITHDRAWAL_EXPLANATION
    assert classify_okx_query("제 출금이 왜 실패했나요?") is WITHDRAWAL_EXPLANATION
    assert classify_okx_query("What is the weather?") is None


def test_withdrawal_contract_is_bounded_and_read_only() -> None:
    policy = WITHDRAWAL_EXPLANATION.safety
    assert policy.max_graph_hops == 4
    assert policy.require_workspace_scope is True
    assert policy.fail_closed_on_missing_required_evidence is True
    assert all(tool.endswith("_read") for tool in policy.allowed_tools)
    assert "authorize_withdrawal" in policy.forbidden_actions
    assert "submit_withdrawal" in policy.forbidden_actions


def test_missing_evidence_stays_explicit() -> None:
    missing = WITHDRAWAL_EXPLANATION.missing_slots(
        {"withdrawal_state": "pending", "network_state": "healthy"}
    )
    assert missing == (
        "account_state",
        "restriction_state",
        "applicable_policy",
        "destination_compatibility",
    )


def test_telemetry_is_versioned_and_does_not_expose_raw_values() -> None:
    supplied = {
        "withdrawal_state": "pending: customer=alice wallet=0xsecret",
        "account_state": "active",
    }
    attrs = WITHDRAWAL_EXPLANATION.telemetry_attributes(
        workspace_id="institution-secret",
        supplied=supplied,
    )

    rendered = repr(attrs)
    assert attrs["seocho.query.family"] == "withdrawal_explanation.v1"
    assert attrs["seocho.prompt.version"] == "1.0.0"
    assert len(attrs["seocho.prompt.template_hash"]) == 16
    assert len(attrs["seocho.workspace_hash"]) == 16
    assert "institution-secret" not in rendered
    assert "alice" not in rendered
    assert "0xsecret" not in rendered


def test_prompt_forbids_action_and_requires_provenance() -> None:
    template = WITHDRAWAL_EXPLANATION.prompt.template.lower()
    assert "provenance" in template
    assert "never authorize" in template
    assert "required slot that is missing" in template
