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
    observed = set()
    for path in DASHBOARD_DIR.glob("seocho-*.json"):
        with open(path, "r", encoding="utf-8") as f:
            observed.add(json.load(f)["uid"])
    assert expected <= observed


def test_critical_dashboard_covers_governance_and_memory_signals() -> None:
    with open(DASHBOARD, "r", encoding="utf-8") as f:
        dashboard = json.load(f)
    assert dashboard["uid"] == "seocho-critical-agent-memory"
    expressions = "\n".join(
        target["expr"]
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


def test_dashboard_labels_do_not_capture_sensitive_content() -> None:
    rendered = DASHBOARD.read_text(encoding="utf-8").lower()
    for forbidden in ("prompt_text", "query_text", "wallet_id", "transaction_payload"):
        assert forbidden not in rendered
