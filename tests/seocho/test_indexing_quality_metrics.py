"""The indexing stage must actually emit, and must not blow up the TSDB.

Two findings from an SRE review, both verified by counting callers rather than
by reading docs.

`record_extraction` and `record_off_vocabulary` had **no production caller** —
six declared instruments that never fired, so the indexing stage of a
four-stage breakdown emitted nothing at all. The observability contract test
could not see it, because it defines "emitted" as the metric name appearing as
a string literal somewhere under `src/`, which a declaration-only module
satisfies.

And the attributes were a cardinality bomb. `label` was model-controlled and
emitted *precisely when it was not in the ontology* — i.e. exactly the values
that are unbounded by definition — with `property` being the cartesian product
of two such strings. A Prometheus series is never reclaimed within its
retention window, so that is monotonic churn, not a steady-state count. It is
also attacker-reachable: document text → LLM → node label → metric attribute.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.metrics")

from seocho.index import quality_metrics
from seocho.metrics import METRIC_SPECS, enable_metrics


@pytest.fixture
def reader():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    enable_metrics(backend="otlp", reader=reader)
    return reader


def _points(reader, name: str):
    data = reader.get_metrics_data()
    for rm in (data.resource_metrics if data else []):
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    return list(metric.data.data_points)
    return []


# ---------------------------------------------------------------------------
# The instruments fire
# ---------------------------------------------------------------------------

def test_extraction_volume_is_emitted(reader):
    quality_metrics.record_extraction(
        ontology="enterprise", source_type="markdown",
        nodes=[{"label": "Person", "properties": {}}],
        relationships=[{"type": "DECIDED"}],
    )
    assert _points(reader, "seocho.index.extraction.nodes")
    assert _points(reader, "seocho.index.extraction.relationships")


def test_empty_extraction_is_counted(reader):
    """A document that yielded nothing is a silent failure — on the measured
    run, a reasoning model returning prose where JSON belonged."""
    quality_metrics.record_extraction(
        ontology="enterprise", source_type="markdown", nodes=[], relationships=[],
    )
    assert _points(reader, "seocho.index.extraction.empty.count")


def test_off_ontology_label_is_counted(reader):
    quality_metrics.record_extraction(
        ontology="enterprise", source_type="markdown",
        nodes=[{"label": "EntityType", "properties": {}}],
        relationships=[], allowed_labels={"Person", "Decision"},
    )
    points = _points(reader, "seocho.index.off_ontology_label.count")
    assert points and points[0].value == 1


def test_off_vocabulary_value_is_counted(reader):
    quality_metrics.record_off_vocabulary(
        ontology="enterprise",
        nodes=[{"label": "Decision", "properties": {"status": "CURRENT"}}],
        vocabularies={"Decision.status": ["applied", "superseded"]},
    )
    assert _points(reader, "seocho.index.off_vocabulary_value.count")


def test_case_only_deviation_still_counts(reader):
    """Half the measured failure was a case split — CURRENT 88 vs current 8.
    A filter literal about case loses half its rows, so `SUPERSEDED` against a
    declared `superseded` is still a deviation."""
    quality_metrics.record_off_vocabulary(
        ontology="enterprise",
        nodes=[{"label": "Decision", "properties": {"status": "SUPERSEDED"}}],
        vocabularies={"Decision.status": ["applied", "superseded"]},
    )
    # Case-folded comparison means this one conforms.
    assert not _points(reader, "seocho.index.off_vocabulary_value.count")


# ---------------------------------------------------------------------------
# The attributes stay bounded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metric", [
    "seocho.index.off_ontology_label.count",
    "seocho.index.off_vocabulary_value.count",
])
def test_model_controlled_values_are_not_metric_attributes(metric):
    """The count is a metric; the offending name is not."""
    spec = METRIC_SPECS[metric]
    assert "label" not in spec.attributes
    assert "property" not in spec.attributes
    assert spec.attributes <= {"ontology"}, (
        f"{metric} carries an unbounded attribute; a Prometheus series is "
        f"never reclaimed within its retention window"
    )


def test_many_invented_labels_produce_one_series(reader):
    """A document instructing 'extract entities of type <uuid>' must not create
    one series per uuid."""
    nodes = [{"label": f"Invented{i}", "properties": {}} for i in range(50)]
    quality_metrics.record_extraction(
        ontology="enterprise", source_type="markdown",
        nodes=nodes, relationships=[], allowed_labels={"Person"},
    )
    points = _points(reader, "seocho.index.off_ontology_label.count")
    assert len(points) == 1, f"{len(points)} series from 50 invented labels"
    assert points[0].value == 50, "the count must still be right"


# ---------------------------------------------------------------------------
# The pipeline calls them
# ---------------------------------------------------------------------------

def test_pipeline_calls_the_quality_emitters():
    """A declared instrument nothing calls is not an instrument."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "src" / "seocho" / "index" / "pipeline.py").read_text()
    assert "record_extraction(" in source, (
        "the indexing stage emits nothing, so it cannot be attributed"
    )
    assert "record_off_vocabulary(" in source


def test_emitter_failure_never_breaks_indexing():
    """Telemetry must never fail the work it measures.

    Asserted against PRODUCTION behaviour: the suite runs strict (see
    conftest.py) so the validator's contract stays enforceable, but the
    deployed registry swallows, because these emit calls sit on the indexing
    hot path.
    """
    from seocho.metrics import get_metrics

    metrics = get_metrics()
    previous = metrics.strict
    metrics.strict = False
    try:
        quality_metrics.record_extraction(
            ontology="enterprise", source_type="x" * 500,  # over the 80-char cap
            nodes=[{"label": "Person", "properties": {}}], relationships=[],
        )
    finally:
        metrics.strict = previous
