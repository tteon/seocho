"""GOPTS F2 — Layer-2 latency harness contract tests (ADR-0097 follow-up).

Pins the math: percentile, geomean, win/regression counting, JSON-
serializable report. End-to-end harness runs with deterministic mock
latency fns so the test stays hermetic.

Live PROFILE-driven runs are F4's scope.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

import pytest

from seocho.eval import gopts_latency
from seocho.eval.gopts_ranking import GoptsFixture


def _make_fixture(fixture_id: str) -> GoptsFixture:
    return GoptsFixture(
        fixture_id=fixture_id,
        question=f"q-{fixture_id}",
        intent="entity_lookup",
        expected_top1_pattern_id="pattern:entity_lookup_by_name",
    )


# --- percentile primitive ----------------------------------------------------


def test_percentile_handles_empty_input() -> None:
    assert gopts_latency._percentile([], 0.5) == 0.0


def test_percentile_p50_picks_middle_value() -> None:
    assert gopts_latency._percentile([10.0, 20.0, 30.0], 0.5) == 20.0


def test_percentile_p95_picks_near_top() -> None:
    assert gopts_latency._percentile([1.0, 2.0, 3.0, 4.0, 100.0], 0.95) == 100.0


def test_percentile_clamps_at_zero_and_one() -> None:
    samples = [5.0, 10.0, 15.0]
    assert gopts_latency._percentile(samples, 0.0) == 5.0
    assert gopts_latency._percentile(samples, 1.0) == 15.0


# --- FixtureLatencyResult derived flags ---------------------------------------


def test_fixture_result_flags_regression_when_gopts_much_slower() -> None:
    """A >10% slowdown at p50 counts as a regression — averaging it away
    would hide enumeration overhead from the operator."""
    result = gopts_latency.FixtureLatencyResult(
        fixture_id="r1",
        repeats=30,
        baseline_p50_ms=10.0,
        baseline_p95_ms=12.0,
        gopts_p50_ms=12.0,  # 20% slower
        gopts_p95_ms=14.0,
        speedup_p50=10.0 / 12.0,
        enumeration_overhead_ms=2.0,
    )
    assert result.is_regression is True
    assert result.is_win is False


def test_fixture_result_flags_win_when_gopts_faster() -> None:
    result = gopts_latency.FixtureLatencyResult(
        fixture_id="w1",
        repeats=30,
        baseline_p50_ms=10.0,
        baseline_p95_ms=12.0,
        gopts_p50_ms=8.0,
        gopts_p95_ms=9.0,
        speedup_p50=10.0 / 8.0,
        enumeration_overhead_ms=-2.0,
    )
    assert result.is_win is True
    assert result.is_regression is False


def test_fixture_result_within_noise_is_neither_win_nor_regression() -> None:
    """A 5% slowdown is noise — not flagged as regression."""
    result = gopts_latency.FixtureLatencyResult(
        fixture_id="n1",
        repeats=30,
        baseline_p50_ms=10.0,
        baseline_p95_ms=12.0,
        gopts_p50_ms=10.5,  # 5% slower — under the 10% bar
        gopts_p95_ms=12.5,
        speedup_p50=10.0 / 10.5,
        enumeration_overhead_ms=0.5,
    )
    assert result.is_regression is False
    assert result.is_win is False  # tied / slightly slower, not a win


# --- run_layer2 end-to-end ---------------------------------------------------


def test_run_layer2_with_uniform_overhead_reports_full_regression() -> None:
    """Honest framing of today's state: GOPTS picks the same plan as
    baseline + adds enumeration overhead. The harness must surface
    this as a 100% regression with sub-1.0 geomean_speedup, not
    average it away."""
    fixtures = [_make_fixture(f"f{i}") for i in range(5)]

    def baseline_fn(_fixture: GoptsFixture) -> float:
        return 10.0  # 10ms baseline, deterministic

    def gopts_fn(_fixture: GoptsFixture) -> float:
        return 12.0  # 12ms — uniform 20% overhead

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=baseline_fn, gopts_fn=gopts_fn, repeats=5
    )

    assert report.metrics.total_fixtures == 5
    assert report.metrics.regression_count == 5  # 20% slowdown > 10% bar
    assert report.metrics.win_rate == 0.0
    assert report.metrics.geomean_speedup < 1.0  # GOPTS slower
    assert report.metrics.mean_enumeration_overhead_ms == pytest.approx(2.0)


def test_run_layer2_with_uniform_speedup_reports_full_win() -> None:
    """When GOPTS *does* pick a faster plan (future F8 scenario), the
    harness reports geomean > 1.0 and full win_rate."""
    fixtures = [_make_fixture(f"f{i}") for i in range(4)]

    def baseline_fn(_fixture: GoptsFixture) -> float:
        return 20.0

    def gopts_fn(_fixture: GoptsFixture) -> float:
        return 10.0  # 2x speedup

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=baseline_fn, gopts_fn=gopts_fn, repeats=10
    )

    assert report.metrics.win_rate == 1.0
    assert report.metrics.regression_count == 0
    assert report.metrics.geomean_speedup == pytest.approx(2.0)
    assert report.metrics.mean_enumeration_overhead_ms == pytest.approx(-10.0)


def test_run_layer2_mixed_results_aggregate_honestly() -> None:
    """Some wins, some regressions, some noise. geomean_speedup must be
    between the extremes; win_rate and regression_count count distinct
    fixtures."""
    fixtures = [_make_fixture(f"f{i}") for i in range(4)]

    # Per-fixture latencies — controlled via dict lookup so each
    # fixture's path produces a different outcome.
    baseline_table: Dict[str, float] = {"f0": 10.0, "f1": 10.0, "f2": 10.0, "f3": 10.0}
    gopts_table: Dict[str, float] = {
        "f0": 5.0,    # win: 2x speedup
        "f1": 20.0,   # regression: 2x slower
        "f2": 10.4,   # noise: 4% slower
        "f3": 10.0,   # noise: tied
    }

    def baseline_fn(fixture: GoptsFixture) -> float:
        return baseline_table[fixture.fixture_id]

    def gopts_fn(fixture: GoptsFixture) -> float:
        return gopts_table[fixture.fixture_id]

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=baseline_fn, gopts_fn=gopts_fn, repeats=3
    )

    assert report.metrics.total_fixtures == 4
    # Only f0 is a strict win (gopts_p50 < baseline_p50). f3 is tied.
    assert report.metrics.win_rate == 0.25
    # Only f1 crosses the 10% regression bar. f2/f3 are noise.
    assert report.metrics.regression_count == 1


def test_run_layer2_drops_fixtures_with_zero_latency_samples() -> None:
    """Wiring error in baseline_fn / gopts_fn (returns 0 or raises)
    drops the fixture from the report instead of corrupting the
    aggregate. Operators see total_fixtures < input count and notice."""
    fixtures = [_make_fixture(f"f{i}") for i in range(3)]

    def baseline_fn(_fixture: GoptsFixture) -> float:
        return 0.0  # always invalid

    def gopts_fn(_fixture: GoptsFixture) -> float:
        return 10.0

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=baseline_fn, gopts_fn=gopts_fn, repeats=3
    )
    assert report.metrics.total_fixtures == 0
    assert report.fixture_results == ()


def test_run_layer2_ignores_individual_sample_exceptions() -> None:
    """Best-effort: a baseline_fn that intermittently raises shouldn't
    blank the entire fixture's measurement — only the bad samples are
    dropped."""
    fixtures = [_make_fixture("f0")]
    call_count = {"baseline": 0}

    def flaky_baseline(_fixture: GoptsFixture) -> float:
        call_count["baseline"] += 1
        if call_count["baseline"] % 2 == 0:
            raise RuntimeError("transient")
        return 10.0

    def gopts_fn(_fixture: GoptsFixture) -> float:
        return 12.0

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=flaky_baseline, gopts_fn=gopts_fn, repeats=10
    )
    assert report.metrics.total_fixtures == 1
    assert report.fixture_results[0].baseline_p50_ms == 10.0


def test_layer2_report_to_dict_is_json_serializable() -> None:
    """Layer2Report.to_dict() lands in JSONL traces — must be
    serializable end-to-end."""
    import json

    fixtures = [_make_fixture("f0"), _make_fixture("f1")]

    def baseline_fn(_fixture: GoptsFixture) -> float:
        return 10.0

    def gopts_fn(_fixture: GoptsFixture) -> float:
        return 11.0

    report = gopts_latency.run_layer2(
        fixtures, baseline_fn=baseline_fn, gopts_fn=gopts_fn, repeats=3
    )
    blob = json.dumps(report.to_dict())
    parsed = json.loads(blob)
    assert parsed["metrics"]["total_fixtures"] == 2
    assert parsed["repeats"] == 3
    assert len(parsed["fixtures"]) == 2


def test_run_layer2_empty_fixtures_returns_zeroed_report() -> None:
    report = gopts_latency.run_layer2(
        [],
        baseline_fn=lambda _f: 10.0,
        gopts_fn=lambda _f: 12.0,
        repeats=3,
    )
    assert report.metrics.total_fixtures == 0
    assert report.metrics.geomean_speedup == 0.0
    assert report.fixture_results == ()
