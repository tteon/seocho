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


def test_record_metric_routes_to_capable_backend() -> None:
    rec = _Recorder()
    try:
        enable_tracing(backend=rec)
        record_metric(
            "seocho_validation_errors",
            3,
            attributes={"mode": "strict", "ontology": "finance"},
        )
        record_metric("seocho_arbiter_route", 1, attributes={"route": "NARRATIVE"})
    finally:
        disable_tracing()

    assert rec.metrics == [
        {
            "name": "seocho_validation_errors",
            "value": 3,
            "attributes": {"mode": "strict", "ontology": "finance"},
        },
        {"name": "seocho_arbiter_route", "value": 1, "attributes": {"route": "NARRATIVE"}},
    ]


def test_record_metric_is_noop_on_flat_backend() -> None:
    flat = _FlatBackend()
    try:
        enable_tracing(backend=flat)
        record_metric("seocho_observations_reified", 5)  # must not raise
    finally:
        disable_tracing()
    assert flat.spans == []


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
