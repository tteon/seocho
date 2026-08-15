"""The observability stack must be loadable, and its alerts must be firable.

Two failure modes actually shipped: a collector config that no YAML parser
accepts (the whole local stack silently received nothing), and paging alerts
written against metrics no code ever emits (unfirable by construction). Both
are contract violations a test can hold, so they get one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from seocho.metrics import METRIC_SPECS

REPO = Path(__file__).resolve().parents[2]
OBSERVABILITY = REPO / "examples" / "observability"

# Metrics owned by third-party exporters (postgres-exporter, etcd); their
# emitters are the containers themselves, not this codebase.
_EXTERNAL_PREFIXES = ("pg_", "etcd_")

# Prometheus decorates OTel instrument names: unit suffixes (_seconds,
# _milliseconds), counter _total, histogram _bucket/_sum/_count. Matching by
# longest spec-derived prefix keeps the test independent of exporter version.
_METRIC_TOKEN = re.compile(r"\bseocho_[a-z0-9_]+")


def _spec_prefixes() -> dict[str, str]:
    """Prometheus-name prefix -> spec name, for longest-prefix resolution."""
    return {spec.name.replace(".", "_"): spec.name for spec in METRIC_SPECS.values()}


def _resolve(token: str, prefixes: dict[str, str]) -> str | None:
    best = None
    for prom, spec_name in prefixes.items():
        if token == prom or token.startswith(prom + "_"):
            if best is None or len(prom) > len(best[0]):
                best = (prom, spec_name)
    return best[1] if best else None


def _emitter_files() -> list[Path]:
    files = []
    for root in (REPO / "src", REPO / "runtime"):
        files.extend(root.rglob("*.py"))
    # The spec table itself declares every name; it is not an emitter.
    return [f for f in files if f.name != "metrics.py" or "seocho" not in str(f.parent)]


def test_every_observability_yaml_parses() -> None:
    yaml_files = sorted(OBSERVABILITY.rglob("*.yaml")) + sorted(OBSERVABILITY.rglob("*.yml"))
    assert yaml_files, "observability overlay is missing"
    for path in yaml_files:
        yaml.safe_load(path.read_text())  # raises on the broken-collector regression


def test_every_grafana_dashboard_parses() -> None:
    dashboards = sorted((OBSERVABILITY / "grafana").rglob("*.json"))
    assert dashboards, "grafana provisioning is missing"
    for path in dashboards:
        json.loads(path.read_text())


def test_collector_pipelines_are_complete() -> None:
    config = yaml.safe_load((OBSERVABILITY / "otel-collector.yaml").read_text())
    pipelines = config["service"]["pipelines"]
    for name in ("traces", "metrics"):
        pipeline = pipelines[name]
        assert pipeline.get("receivers"), f"{name} pipeline has no receivers"
        assert pipeline.get("exporters"), f"{name} pipeline has no exporters"


def test_alert_rules_only_reference_emitted_metrics() -> None:
    """Every seocho_* metric in prometheus-rules.yml maps to a METRIC_SPECS
    entry that at least one non-test module actually emits.

    An alert on a never-emitted metric is worse than no alert: it reads as
    coverage while being unfirable by construction.
    """
    rules_text = (OBSERVABILITY / "prometheus-rules.yml").read_text()
    tokens = sorted(set(_METRIC_TOKEN.findall(rules_text)))
    assert tokens, "no seocho_ metrics referenced by the rules?"

    prefixes = _spec_prefixes()
    sources = {path: path.read_text() for path in _emitter_files()}

    unresolved: list[str] = []
    unemitted: list[str] = []
    for token in tokens:
        if token.startswith(_EXTERNAL_PREFIXES):
            continue
        spec_name = _resolve(token, prefixes)
        if spec_name is None:
            unresolved.append(token)
            continue
        needle = f'"{spec_name}"'
        if not any(needle in text for text in sources.values()):
            unemitted.append(f"{token} -> {spec_name}")

    assert not unresolved, f"rules reference metrics outside METRIC_SPECS: {unresolved}"
    assert not unemitted, (
        "alert rules depend on metrics nothing emits (src/ or runtime/): "
        + ", ".join(unemitted)
    )


def test_dashboards_only_reference_cataloged_metrics() -> None:
    """Dashboard panels must at least point at cataloged metric names.

    Emitter coverage for dashboards is tracked separately (seocho-7ej adds the
    DB/host saturation layer); what this pins is the cheaper invariant that a
    panel never invents a metric name outside the ADR-0146 catalog — the
    ``_pending_entries`` drift is exactly what that catches.
    """
    prefixes = _spec_prefixes()
    unresolved: list[str] = []
    for path in sorted((OBSERVABILITY / "grafana").rglob("*.json")):
        for token in set(_METRIC_TOKEN.findall(path.read_text())):
            if token.startswith(_EXTERNAL_PREFIXES):
                continue
            if _resolve(token, prefixes) is None:
                unresolved.append(f"{path.name}: {token}")
    assert not unresolved, f"dashboard metrics missing from METRIC_SPECS: {unresolved}"
