from seocho.coordination import active_policy_record, projection_watermark_record
from seocho.query.workload_compiler import compile_workload_query
from seocho.query.workloads import TRANSACTION_RISK_PREFLIGHT
from seocho.risk import (
    RiskPolicy,
    RiskSignalEvidence,
    default_disclosure_policy,
    evaluate_risk_preflight,
)


policy_pointer = active_policy_record(
    workspace_id="demo-institution",
    policy_id="wallet-risk",
    policy_version="3.1.0",
)
watermark_pointer = projection_watermark_record(
    workspace_id="demo-institution",
    projection="risk-graph",
    watermark="fdb:12345",
)

plan = compile_workload_query(
    TRANSACTION_RISK_PREFLIGHT,
    workspace_id="demo-institution",
    input_slots={
        "customer_id": "demo-customer",
        "destination_wallet_hash": "sha256:demo-wallet",
    },
)

result = evaluate_risk_preflight(
    signals=(
        RiskSignalEvidence(
            reason_code="SANCTIONED_COUNTERPARTY",
            severity="critical",
            graph_hops=2,
            provenance_id="demo-risk-source",
        ),
    ),
    repeated_flagged_counterparties=1,
    policy=RiskPolicy(policy_id="wallet-risk", version="3.1.0"),
    projection_current=True,
)

customer_view = default_disclosure_policy().filter_record(
    {
        "disposition": result.disposition,
        "reason_codes": list(result.reason_codes),
        "policy_version": result.policy_version,
        "graph_hops": result.max_observed_hops,
        "provenance_id": "demo-risk-source",
        "customer_id": "demo-customer",
        "wallet_hash": "sha256:demo-wallet",
        "internal_risk_score": 0.99,
        "policy_threshold": 0.95,
    },
    role="customer",
)

print("coordination:", policy_pointer.kind, watermark_pointer.kind)
print("query tier:", plan.tier, "max rows:", plan.params["limit"])
print("customer view:", dict(customer_view.visible))
print("redacted fields:", customer_view.redacted_fields)
