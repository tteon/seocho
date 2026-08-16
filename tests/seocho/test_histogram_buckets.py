"""Every histogram needs boundaries shaped for its unit.

The bucket views matched on a NAME SUFFIX: `*.duration` with unit `s`, and
`*.latency` with unit `ms`. That covered 12 of 30 histograms. The other 18 fell
back to OTel's default boundaries, which run 0..10000 and are shaped for
milliseconds.

Two instruments show why the suffix rule was the wrong axis:

  seocho.gen_ai.time_to_first_token       unit s,  no `.duration` suffix
  seocho.memory.commit.phase.duration     `.duration` suffix, unit ms

Neither matched. TTFT is the only prefill signal a hosted API exposes, and with
default boundaries a 0.8 s observation lands in the (0, 5] bucket, so p95 reads
about 5 s no matter what the service does. Ratio histograms in [0, 1] collapsed
into the first bucket entirely, making `server_share` and `provenance.coverage`
unreadable.

Unit is what determines the right boundaries, so views match on unit and a new
instrument gets sensible buckets by declaring one.
"""

from __future__ import annotations

import pytest

from seocho.metrics import METRIC_SPECS, build_histogram_views, enable_metrics

pytest.importorskip("opentelemetry.sdk.metrics")


def _view_units() -> set:
    return {view._instrument_unit for view in build_histogram_views()}


def test_every_declared_histogram_unit_has_a_view():
    histograms = [s for s in METRIC_SPECS.values() if s.kind == "histogram"]
    assert histograms, "metric catalog declares no histograms"

    uncovered = sorted(
        {s.unit for s in histograms} - _view_units()
    )
    assert not uncovered, (
        f"histograms with units {uncovered} fall back to OTel's default "
        f"millisecond-shaped boundaries"
    )


@pytest.mark.parametrize("unit", ["s", "ms", "1", "{token}", "{item}"])
def test_expected_units_are_covered(unit):
    assert unit in _view_units()


def _bucket_for(reader, metric_name: str, value: float):
    """Return the (low, high] bucket a value landed in."""
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != metric_name:
                    continue
                point = list(metric.data.data_points)[0]
                bounds = list(point.explicit_bounds)
                index = next(
                    i for i, count in enumerate(point.bucket_counts) if count
                )
                low = bounds[index - 1] if index else 0.0
                high = bounds[index] if index < len(bounds) else float("inf")
                return low, high
    raise AssertionError(f"{metric_name} was not exported")


@pytest.fixture
def reader():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    return InMemoryMetricReader()


def test_time_to_first_token_lands_in_a_sub_second_bucket(reader):
    """The regression that made the only prefill signal unreadable."""
    metrics = enable_metrics(backend="otlp", reader=reader)
    metrics.record(
        "seocho.gen_ai.time_to_first_token", 0.8,
        {"gen_ai.provider.name": "mara", "gen_ai.request.model": "MiniMax-M2.5"},
    )

    low, high = _bucket_for(reader, "seocho.gen_ai.time_to_first_token", 0.8)
    assert high <= 1.0, (
        f"0.8s landed in ({low}, {high}] — with default boundaries this is "
        f"(0, 5] and p95 reads ~5s regardless of reality"
    )


def test_ratio_histogram_resolves_within_the_unit_interval(reader):
    """A share in [0, 1] must not collapse into one bucket."""
    metrics = enable_metrics(backend="otlp", reader=reader)
    metrics.record(
        "db.client.operation.server_share", 0.35,
        {"db.system": "neo4j", "operation": "query"},
    )

    low, high = _bucket_for(reader, "db.client.operation.server_share", 0.35)
    assert low >= 0.3 and high <= 0.5, (
        f"0.35 landed in ({low}, {high}] — ratios need sub-unit boundaries"
    )


def test_millisecond_duration_is_not_treated_as_seconds(reader):
    """`*.duration` in ms matched no view under the old name-suffix rule."""
    metrics = enable_metrics(backend="otlp", reader=reader)
    metrics.record(
        "seocho.memory.commit.phase.duration", 12.0,
        {"phase": "write", "outcome": "ok"},
    )

    low, high = _bucket_for(reader, "seocho.memory.commit.phase.duration", 12.0)
    assert 10.0 <= low and high <= 25.0, (
        f"12ms landed in ({low}, {high}]; expected the millisecond boundaries"
    )
