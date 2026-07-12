from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "examples" / "observability" / "prometheus-rules.yml"


def test_rules_cover_slo_freshness_governance_and_projection() -> None:
    payload = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = [rule for group in payload["groups"] for rule in group["rules"]]
    records = {rule["record"] for rule in rules if "record" in rule}
    alerts = {rule["alert"] for rule in rules if "alert" in rule}
    assert {
        "seocho:agent_error_ratio:5m",
        "seocho:projection_lag_events",
        "seocho:projection_stalled",
        "seocho:customer_query_bad_ratio:5m",
        "seocho:customer_query_bad_ratio:1h",
    } <= records
    assert {
        "SeochoSilentStaleAnswer",
        "SeochoDisclosureViolation",
        "SeochoProjectionStalled",
        "SeochoAgentErrorBudgetBurn",
        "SeochoPostgreSQLDown",
        "SeochoEtcdNoLeader",
        "SeochoCustomerQueryFastBurn",
    } <= alerts


def test_production_alerts_do_not_page_on_evaluation_scenarios() -> None:
    rendered = RULES.read_text(encoding="utf-8")
    assert "seocho_critical_" not in rendered
    assert "workspace_id" not in rendered
    assert 'traffic_type="production"' in rendered
