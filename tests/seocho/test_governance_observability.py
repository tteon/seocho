"""ADR-0144 / seocho-d6x.4: governance metrics + guardrail audit span.

Covers the metrics counter API (record_metric) and the guardrail-selector audit
span. The extraction-span enrichment (enforcement_mode + validation detail) is
exercised by the existing pipeline tests; here we cover the standalone pieces.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from seocho.guardrail_selector import select_guardrail
from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_scorecard import build_corpus_profile
from seocho.tracing import (
    TracingBackend,
    disable_tracing,
    enable_tracing,
    record_metric,
)


class _Recorder(TracingBackend):
    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []
        self.metrics: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append(
            {"name": name, "output": output_data or {}, "metadata": metadata or {}}
        )

    def record_metric(
        self,
        name: str,
        value: float = 1,
        *,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.metrics.append({"name": name, "value": value, "attributes": attributes or {}})


class _FlatBackend(TracingBackend):
    """A backend that does NOT implement record_metric."""

    def __init__(self) -> None:
        self.spans: List[str] = []

    def log_span(self, name: str, **_kw: Any) -> None:
        self.spans.append(name)


class _Instrument:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def add(self, value, attributes=None):
        self.calls.append((value, attributes))

    record = add
    set = add


class _Meter:
    def __init__(self) -> None:
        self.instruments: Dict[str, _Instrument] = {}

    def _make(self, name, **kwargs):
        self.instruments[name] = _Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_gauge = _make
    create_histogram = _make


def test_record_metric_translates_legacy_names_into_the_registry(monkeypatch) -> None:
    """record_metric is now a shim over the ADR-0146 registry: legacy
    snake_case names map onto catalog specs, one pipeline for everything."""
    import seocho.metrics as metrics_module

    meter = _Meter()
    monkeypatch.setattr(
        metrics_module, "_metrics", metrics_module.ProductionMetrics(meter)
    )
    record_metric(
        "seocho_validation_errors",
        3,
        attributes={"mode": "strict", "ontology": "finance"},
    )
    record_metric("seocho_arbiter_route", 1, attributes={"route": "NARRATIVE"})

    validation = meter.instruments["seocho.index.validation_errors.count"].calls
    route = meter.instruments["seocho.arbiter.route.count"].calls
    assert validation == [(3, {"mode": "strict", "ontology": "finance"})]
    assert route == [(1, {"route": "NARRATIVE"})]


def test_record_metric_drops_uncataloged_names(monkeypatch) -> None:
    """An arbitrary name would bypass the registry's label budget — the whole
    reason the two pipelines were unified — so it is dropped, not emitted."""
    import seocho.metrics as metrics_module

    meter = _Meter()
    monkeypatch.setattr(
        metrics_module, "_metrics", metrics_module.ProductionMetrics(meter)
    )
    record_metric("seocho_totally_new_counter", 5)  # must not raise
    assert all(not inst.calls for inst in meter.instruments.values())


def _lean() -> Ontology:
    return Ontology(
        "lean",
        nodes={
            "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
        },
    )


def _rich() -> Ontology:
    return Ontology(
        "rich",
        nodes={
            "Company": NodeDef(description="A company.", properties={"name": P(str, unique=True)}),
            "Person": NodeDef(description="A person.", properties={"name": P(str, unique=True)}),
            "Regulation": NodeDef(description="A rule.", properties={"name": P(str, unique=True)}),
            "Risk": NodeDef(description="A risk.", properties={"name": P(str, unique=True)}),
        },
    )


def test_guardrail_select_emits_audit_span() -> None:
    corpus = build_corpus_profile(
        [
            {"nodes": [{"label": "Person"}, {"label": "Regulation"}]},
            {"nodes": [{"label": "Risk"}, {"label": "Person"}]},
        ]
    )
    rec = _Recorder()
    try:
        enable_tracing(backend=rec)
        recommendation = select_guardrail({"lean": _lean(), "rich": _rich()}, corpus)
    finally:
        disable_tracing()

    audit = next(s for s in rec.spans if s["name"] == "ontology.guardrail_select")
    assert audit["output"]["chosen"] == recommendation.chosen
    assert "domain_kind" in audit["output"]
    assert "rationale" in audit["metadata"]
    assert "candidate_scores" in audit["metadata"]
