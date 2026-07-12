import pytest

from seocho.query.workload_compiler import (
    compile_workload_query,
    fallback_policy_for,
    validate_text2cypher_fallback,
    validate_workload_query,
)
from seocho.query.workloads import TRANSACTION_RISK_PREFLIGHT, WITHDRAWAL_EXPLANATION


def test_known_workload_uses_parameterized_recipe() -> None:
    plan = compile_workload_query(
        WITHDRAWAL_EXPLANATION,
        workspace_id="tenant-a",
        input_slots={"withdrawal_id": "wd-123"},
        limit=500,
    )
    assert plan.tier == "approved_recipe"
    assert "$workspace_id" in plan.cypher
    assert "$withdrawal_id" in plan.cypher
    assert "tenant-a" not in plan.cypher
    assert "wd-123" not in plan.cypher
    assert plan.params == {
        "workspace_id": "tenant-a",
        "withdrawal_id": "wd-123",
        "limit": 50,
    }
    assert plan.max_repair_attempts == 0


def test_recipe_requires_tenant_and_primary_identifier() -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        compile_workload_query(
            WITHDRAWAL_EXPLANATION,
            workspace_id="",
            input_slots={"withdrawal_id": "wd-123"},
        )
    with pytest.raises(ValueError, match="withdrawal_id"):
        compile_workload_query(
            WITHDRAWAL_EXPLANATION,
            workspace_id="tenant-a",
            input_slots={},
        )


def test_validator_rejects_write_and_unbounded_contracts() -> None:
    violations = validate_workload_query(
        "MATCH (n) DELETE n RETURN n",
        required_parameters=("workspace_id",),
        max_graph_hops=4,
    )
    assert "forbidden_token:delete" in violations
    assert "missing_parameter:workspace_id" in violations
    assert "missing_parameterized_limit" in violations


def test_text2cypher_fallback_is_schema_bounded_and_one_repair_only() -> None:
    policy = fallback_policy_for(WITHDRAWAL_EXPLANATION)
    assert policy.max_graph_hops == 4
    assert policy.max_repair_attempts == 1
    assert policy.require_explain_before_execute is True
    assert policy.required_parameters == ("workspace_id",)
    assert "Withdrawal" in policy.allowed_labels
    assert "BLOCKED_BY" in policy.allowed_relationships


def test_text2cypher_fallback_accepts_only_bounded_tenant_scoped_query() -> None:
    policy = fallback_policy_for(WITHDRAWAL_EXPLANATION)
    cypher = """MATCH (w:Withdrawal {workspace_id: $workspace_id})
    MATCH (w)-[:BLOCKED_BY*1..2]->(r:Restriction)
    RETURN r.code LIMIT $limit"""
    assert validate_text2cypher_fallback(
        cypher,
        params={"workspace_id": "tenant-a", "limit": 25},
        policy=policy,
    ) == ()


def test_text2cypher_fallback_rejects_schema_fanout_and_result_budget() -> None:
    policy = fallback_policy_for(WITHDRAWAL_EXPLANATION)
    cypher = """MATCH (w:Wallet)-[:UNKNOWN*]->(r:Restriction)
    RETURN r LIMIT $limit"""
    violations = validate_text2cypher_fallback(
        cypher,
        params={"limit": 500},
        policy=policy,
    )
    assert "unbounded_graph_path" in violations
    assert "missing_parameter:workspace_id" in violations
    assert "missing_parameter_value:workspace_id" in violations
    assert "unknown_labels:Wallet" in violations
    assert "unknown_relationships:UNKNOWN" in violations
    assert "result_limit_exceeded" in violations


def test_risk_preflight_recipe_is_hop_bounded_and_parameterized() -> None:
    plan = compile_workload_query(
        TRANSACTION_RISK_PREFLIGHT,
        workspace_id="tenant-a",
        input_slots={
            "customer_id": "customer-secret",
            "destination_wallet_hash": "wallet-hash-secret",
        },
    )
    assert plan.tier == "approved_recipe"
    assert "*1..4" in plan.cypher
    assert "$customer_id" in plan.cypher
    assert "$destination_wallet_hash" in plan.cypher
    assert "customer-secret" not in plan.cypher
    assert "wallet-hash-secret" not in plan.cypher
    assert plan.params["customer_id"] == "customer-secret"
    assert plan.params["destination_wallet_hash"] == "wallet-hash-secret"
