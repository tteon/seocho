import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = (
    ROOT
    / "examples"
    / "observability"
    / "dashboards"
    / "seocho-critical-agent-memory.json"
)


def test_critical_dashboard_covers_governance_and_memory_signals() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
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
