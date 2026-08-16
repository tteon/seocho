"""The instruments must fire through a real meter, not just through a fake.

Every other metrics test substitutes a recorder, which proves the emitter was
called and nothing about whether the value survives OpenTelemetry's validation.
That gap hid a real defect: `record_scorecard` read `data["score"]` while
`OntologyScorecard.to_dict()` writes `overall_score`, so the one panel answering
"is this ontology good enough" silently had no data while a grade was still
produced and everything looked healthy.

This drives an in-memory MeterProvider so the assertions are about what a
Grafana dashboard would actually receive.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402

import seocho.metrics as metrics_module  # noqa: E402
from seocho import NodeDef, Ontology, P  # noqa: E402
from seocho.index import quality_metrics as qm  # noqa: E402
from seocho.ontology_scorecard import score_ontology  # noqa: E402


@pytest.fixture
def emitted(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(metrics_module, "_metrics",
                        metrics_module.ProductionMetrics(provider.get_meter("test")))

    def collect():
        seen = {}
        for resource in reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    points = list(metric.data.data_points)
                    seen[metric.name] = points[0] if points else None
        return seen

    return collect


def _ontology() -> Ontology:
    return Ontology(
        name="probe",
        nodes={
            "Person": NodeDef(description="a person",
                              properties={"name": P(str, unique=True)},
                              identity_keys=["name"]),
            "Decision": NodeDef(description="a decision",
                                properties={"name": P(str, unique=True), "status": P(str)}),
        },
    )


def test_the_scorecard_score_reaches_a_real_meter(emitted):
    """The regression: a wrong key name made this emit nothing, silently."""
    qm.record_scorecard(score_ontology(_ontology()), ontology="probe")
    seen = emitted()
    assert "seocho.ontology.scorecard.score" in seen, sorted(seen)
    point = seen["seocho.ontology.scorecard.score"]
    assert point is not None and 0.0 <= point.value <= 1.0


def test_dimensions_and_weak_points_reach_a_real_meter(emitted):
    qm.record_scorecard(score_ontology(_ontology()), ontology="probe")
    seen = emitted()
    assert "seocho.ontology.scorecard.dimension" in seen
    assert "seocho.ontology.weak_point.count" in seen


def test_contract_gaps_reach_a_real_meter(emitted):
    qm.record_contract_gaps(_ontology(), ontology="probe")
    assert "seocho.ontology.contract.missing" in emitted()


def test_extraction_signals_reach_a_real_meter(emitted):
    qm.record_extraction(
        ontology="probe", source_type="slack",
        nodes=[{"label": "Person"}, {"label": "NotDeclared"}],
        relationships=[], allowed_labels={"Person", "Decision"},
        retries=1, retry_reason="non_json_response",
    )
    seen = emitted()
    for name in ("seocho.index.extraction.nodes",
                 "seocho.index.extraction.relationships",
                 "seocho.index.extraction.retry.count",
                 "seocho.index.off_ontology_label.count"):
        assert name in seen, f"{name} missing from {sorted(seen)}"


def test_off_vocabulary_reaches_a_real_meter(emitted):
    qm.record_off_vocabulary(
        ontology="probe",
        nodes=[{"label": "Decision", "properties": {"status": "CURRENT"}}],
        vocabularies={"Decision.status": ["proposed", "applied", "superseded"]},
    )
    assert "seocho.index.off_vocabulary_value.count" in emitted()


def test_attribute_values_survive_the_bounded_contract(emitted):
    """ProductionMetrics rejects unbounded attributes; ours must pass."""
    qm.record_extraction(ontology="probe", source_type="gmail",
                         nodes=[{"label": "Ghost"}], relationships=[],
                         allowed_labels={"Person"})
    point = emitted()["seocho.index.off_ontology_label.count"]
    assert dict(point.attributes)["label"] == "Ghost"
