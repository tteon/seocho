import pytest

from seocho.coordination import (
    CoordinationRecord,
    active_policy_record,
    projection_watermark_record,
    projector_lease_record,
)


def test_etcd_records_hold_pointers_not_customer_data() -> None:
    policy = active_policy_record(
        workspace_id="institution-secret",
        policy_id="wallet-risk",
        policy_version="3.1.0",
    )
    watermark = projection_watermark_record(
        workspace_id="institution-secret",
        projection="risk-graph",
        watermark="fdb:12345",
    )
    rendered = repr((policy, watermark))
    assert policy.kind == "active_policy"
    assert watermark.kind == "projection_watermark"
    assert "institution-secret" not in rendered
    assert policy.value == {"policy_id": "wallet-risk", "policy_version": "3.1.0"}


@pytest.mark.parametrize("field", ["customer_id", "wallet", "transaction", "risk_signal"])
def test_etcd_contract_rejects_customer_and_risk_payloads(field: str) -> None:
    record = CoordinationRecord(
        kind="active_policy",
        key="/seocho/workspaces/safe/policy/active",
        value={field: "secret"},
    )
    with pytest.raises(ValueError, match="customer data"):
        record.validate()


def test_etcd_contract_rejects_large_values() -> None:
    record = CoordinationRecord(
        kind="active_policy",
        key="/seocho/workspaces/safe/policy/active",
        value={"policy_id": "x" * 9000},
    )
    with pytest.raises(ValueError, match="8 KiB"):
        record.validate()


def test_projector_lease_contains_only_worker_and_fencing_metadata() -> None:
    record = projector_lease_record(
        workspace_id="ws-1",
        projection="neo4j",
        worker_id="projector-1",
        fencing_token=7,
    )

    assert record.kind == "worker_lease"
    assert record.value == {"worker_id": "projector-1", "fencing_token": 7}
