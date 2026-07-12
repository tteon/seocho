import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "examples" / "observability" / "grafana" / "provisioning" / "dashboards"
DASHBOARD = (
    DASHBOARD_DIR
    / "seocho-evaluation.json"
)


def test_seven_production_and_evaluation_dashboards_are_provisioned() -> None:
    expected = {
        "seocho-service-overview",
        "seocho-memory-consistency",
        "seocho-graphrag-context",
        "seocho-llm-agent-exchange",
        "seocho-governance-audit",
        "seocho-dependencies",
        "seocho-critical-agent-memory",
    }
    observed = {
        json.loads(path.read_text(encoding="utf-8"))["uid"]
        for path in DASHBOARD_DIR.glob("seocho-*.json")
    }
    assert expected <= observed


def test_critical_dashboard_covers_governance_and_memory_signals() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert dashboard["uid"] == "seocho-critical-agent-memory"
    expressions = "\n".join(
        target.get("expr", "")
        for panel in dashboard["panels"]
        for target in panel.get("targets", ())
    )
    assert "seocho_critical_scenario_passed_total" in expressions
    assert "seocho_critical_projection_lag" in expressions
    assert "seocho_critical_silent_stale_total" in expressions
    assert "seocho_critical_disclosure_violations_total" in expressions
    assert "seocho_critical_latency_milliseconds_bucket" in expressions
    assert "seocho_critical_memory_sequence" in expressions
    assert "seocho_critical_projection_watermark" in expressions
    assert "seocho_evaluation_query_accuracy_ratio" in expressions
    assert "seocho_evaluation_context_reduction_ratio" in expressions
    assert "seocho_evaluation_scenario_status_ratio" in expressions
    assert "seocho_evaluation_capability_status_ratio" in expressions
    trace_panels = [
        panel for panel in dashboard["panels"]
        if panel.get("datasource", {}).get("type") == "tempo"
    ]
    assert len(trace_panels) == 1
    assert "seocho-(evaluation|okx-live)" in trace_panels[0]["targets"][0]["query"]


def test_dashboard_labels_do_not_capture_sensitive_content() -> None:
    rendered = DASHBOARD.read_text(encoding="utf-8").lower()
    for forbidden in ("prompt_text", "query_text", "wallet_id", "transaction_payload"):
        assert forbidden not in rendered
