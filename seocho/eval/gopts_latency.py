"""GOPTS F2 — Layer-2 wall-clock latency harness (ADR-0097 follow-up).

Layer 1 (``gopts_ranking``) answers "does the cost model rank correctly?";
Layer 2 answers "does the chosen plan run faster than the pre-G2 baseline?"

Honest framing for today's catalog state: the GOPTS cost ranker picks the
same top-1 plan that the pre-G2 dispatcher would have picked (F1's
declared alternatives don't flip the ranker's preferences). So the
expected Layer-2 result today is **GOPTS is slower by the enumeration
overhead** — geomean_speedup < 1.0, regression_count = total fixtures.

That is the right thing to measure now: it pins the enumeration cost so
future work that *does* change picks (F8 multi-plan execution, or
richer alternatives) has a concrete bar to clear. When a future caller
wires ``baseline_fn`` and ``gopts_fn`` against a populated DB and sees
geomean_speedup ≥ 1.2× with win_rate ≥ 60%, the cost ranker is
delivering on its promise.

The harness is execution-source-agnostic: ``baseline_fn`` and
``gopts_fn`` are callables that accept a ``GoptsFixture`` and return a
single-run latency in milliseconds. Unit tests stub them with
deterministic values; live integration runs wrap real Cypher execution
against DozerDB.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence, Tuple

from .gopts_ranking import GoptsFixture


# Type alias: caller-supplied function that runs one execution of either
# the baseline or the GOPTS path and returns wall-clock latency in ms.
LatencyFn = Callable[[GoptsFixture], float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile, no interpolation.

    Cheap and deterministic — F2 doesn't need numpy. p is in [0, 1].
    """
    if not values:
        return 0.0
    s = sorted(values)
    if p <= 0.0:
        return s[0]
    if p >= 1.0:
        return s[-1]
    idx = int(round(p * (len(s) - 1)))
    return s[idx]


# ---------------------------------------------------------------------------
# Per-fixture record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureLatencyResult:
    """One fixture's measured baseline vs GOPTS latencies."""

    fixture_id: str
    repeats: int
    baseline_p50_ms: float
    baseline_p95_ms: float
    gopts_p50_ms: float
    gopts_p95_ms: float
    speedup_p50: float          # baseline_p50 / gopts_p50
    enumeration_overhead_ms: float  # gopts_p50 - baseline_p50

    @property
    def is_regression(self) -> bool:
        """A fixture is a regression when GOPTS is materially slower
        than baseline at p50 (>10%). Pre-ADR-0097 callers also need
        to surface this — averaging away regressions is dishonest."""
        if self.baseline_p50_ms <= 0:
            return False
        return self.gopts_p50_ms > self.baseline_p50_ms * 1.10

    @property
    def is_win(self) -> bool:
        """A fixture is a win when GOPTS is faster than baseline at p50."""
        return self.gopts_p50_ms < self.baseline_p50_ms


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer2Metrics:
    """Suite-level aggregates per ADR-0097 Layer-2 targets.

    geomean_speedup: geometric mean of baseline_p50/gopts_p50 across
        fixtures. Target ≥ 1.2× when ranker picks meaningfully different
        plans. Currently expected < 1.0 (pure enumeration overhead).
    win_rate: fraction of fixtures where GOPTS is faster at p50.
    regression_count: fixtures where GOPTS is materially slower (>10%
        at p50). Reported per-suite — must not be averaged into geomean.
    mean_enumeration_overhead_ms: average per-fixture overhead. Pins the
        cost-ranker overhead so future improvements have a concrete
        threshold to clear.
    """

    geomean_speedup: float
    win_rate: float
    regression_count: int
    total_fixtures: int
    mean_enumeration_overhead_ms: float
    median_enumeration_overhead_ms: float


@dataclass(frozen=True)
class Layer2Report:
    fixture_results: Tuple[FixtureLatencyResult, ...]
    metrics: Layer2Metrics
    repeats: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repeats": self.repeats,
            "metrics": {
                "geomean_speedup": self.metrics.geomean_speedup,
                "win_rate": self.metrics.win_rate,
                "regression_count": self.metrics.regression_count,
                "total_fixtures": self.metrics.total_fixtures,
                "mean_enumeration_overhead_ms": self.metrics.mean_enumeration_overhead_ms,
                "median_enumeration_overhead_ms": self.metrics.median_enumeration_overhead_ms,
            },
            "fixtures": [
                {
                    "fixture_id": fr.fixture_id,
                    "repeats": fr.repeats,
                    "baseline_p50_ms": fr.baseline_p50_ms,
                    "baseline_p95_ms": fr.baseline_p95_ms,
                    "gopts_p50_ms": fr.gopts_p50_ms,
                    "gopts_p95_ms": fr.gopts_p95_ms,
                    "speedup_p50": fr.speedup_p50,
                    "enumeration_overhead_ms": fr.enumeration_overhead_ms,
                    "is_win": fr.is_win,
                    "is_regression": fr.is_regression,
                }
                for fr in self.fixture_results
            ],
        }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def run_layer2(
    fixtures: Sequence[GoptsFixture],
    *,
    baseline_fn: LatencyFn,
    gopts_fn: LatencyFn,
    repeats: int = 30,
) -> Layer2Report:
    """Measure GOPTS vs baseline wall-clock latency across the fixture suite.

    For each fixture, runs both paths ``repeats`` times, computes p50/p95
    per path, then aggregates speedup / win_rate / regression_count
    across fixtures. ``baseline_fn`` and ``gopts_fn`` are caller-supplied
    so the same harness drives unit tests (deterministic mocks) and
    live integration runs (real Cypher execution).

    Fixtures producing zero or negative latencies are skipped — they
    indicate a wiring error in baseline_fn / gopts_fn, not a real
    measurement.
    """
    results: List[FixtureLatencyResult] = []
    for fixture in fixtures:
        baseline_samples = _collect_samples(baseline_fn, fixture, repeats)
        gopts_samples = _collect_samples(gopts_fn, fixture, repeats)
        if not baseline_samples or not gopts_samples:
            continue
        baseline_p50 = _percentile(baseline_samples, 0.50)
        baseline_p95 = _percentile(baseline_samples, 0.95)
        gopts_p50 = _percentile(gopts_samples, 0.50)
        gopts_p95 = _percentile(gopts_samples, 0.95)
        speedup_p50 = (
            baseline_p50 / gopts_p50 if gopts_p50 > 0 else 0.0
        )
        overhead = gopts_p50 - baseline_p50
        results.append(
            FixtureLatencyResult(
                fixture_id=fixture.fixture_id,
                repeats=repeats,
                baseline_p50_ms=baseline_p50,
                baseline_p95_ms=baseline_p95,
                gopts_p50_ms=gopts_p50,
                gopts_p95_ms=gopts_p95,
                speedup_p50=speedup_p50,
                enumeration_overhead_ms=overhead,
            )
        )

    if not results:
        return Layer2Report(
            fixture_results=(),
            metrics=Layer2Metrics(
                geomean_speedup=0.0,
                win_rate=0.0,
                regression_count=0,
                total_fixtures=0,
                mean_enumeration_overhead_ms=0.0,
                median_enumeration_overhead_ms=0.0,
            ),
            repeats=repeats,
        )

    valid_speedups = [r.speedup_p50 for r in results if r.speedup_p50 > 0]
    geomean = statistics.geometric_mean(valid_speedups) if valid_speedups else 0.0
    win_rate = sum(1 for r in results if r.is_win) / len(results)
    regression_count = sum(1 for r in results if r.is_regression)
    overheads = [r.enumeration_overhead_ms for r in results]
    mean_overhead = statistics.fmean(overheads)
    median_overhead = statistics.median(overheads)

    return Layer2Report(
        fixture_results=tuple(results),
        metrics=Layer2Metrics(
            geomean_speedup=geomean,
            win_rate=win_rate,
            regression_count=regression_count,
            total_fixtures=len(results),
            mean_enumeration_overhead_ms=mean_overhead,
            median_enumeration_overhead_ms=median_overhead,
        ),
        repeats=repeats,
    )


def _collect_samples(
    fn: LatencyFn,
    fixture: GoptsFixture,
    repeats: int,
) -> List[float]:
    """Run ``fn(fixture)`` ``repeats`` times, drop non-positive samples."""
    samples: List[float] = []
    for _ in range(max(1, repeats)):
        try:
            value = float(fn(fixture))
        except Exception:
            # Individual sample errors don't blank the suite — caller
            # may be using a flaky live endpoint. Negative/zero samples
            # are equally suspicious.
            continue
        if value > 0:
            samples.append(value)
    return samples
