"""Plan-quality signals must reach the pipeline, sampled and off by default.

`query/plan_quality` shipped with ADR-0144 and had no production caller: it was
imported only by a benchmark script. Its own docstring says the signals "belong
on the span rather than in a benchmark script", and the reason is measured —
two queries returning identical answers cost 25 db hits and 6.6M at SF1000,
while at SF1 the same pair is 4 ms apart. Latency finds that after the graph has
grown; the seek/scan distinction finds it before.

PROFILE re-runs the query with full accounting, so it is sampled. These pin the
three properties that make the wiring safe to leave in: it does nothing unless
asked, it never fails a query, and when it does run it emits.
"""

from __future__ import annotations

import pytest

from seocho.local_engine import _LocalEngine


class _Executor:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def execute(self, plan):
        self.seen.append(plan.cypher)

        class _R:
            records = [{"profile": {"dbHits": 25, "operatorType": "NodeIndexSeek"}}]
            error = None

        return _R()


def _engine() -> _LocalEngine:
    return object.__new__(_LocalEngine)


def test_profiling_is_off_by_default(monkeypatch):
    """An unset sample rate must not add a second query to every request."""
    monkeypatch.delenv("SEOCHO_PLAN_PROFILE_SAMPLE", raising=False)
    executor = _Executor()
    _engine()._record_plan_quality("MATCH (n) RETURN n", {}, "neo4j", executor=executor)
    assert executor.seen == []


def test_a_zero_rate_is_off(monkeypatch):
    monkeypatch.setenv("SEOCHO_PLAN_PROFILE_SAMPLE", "0")
    executor = _Executor()
    _engine()._record_plan_quality("MATCH (n) RETURN n", {}, "neo4j", executor=executor)
    assert executor.seen == []


def test_a_malformed_rate_is_off_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("SEOCHO_PLAN_PROFILE_SAMPLE", "not-a-number")
    executor = _Executor()
    _engine()._record_plan_quality("MATCH (n) RETURN n", {}, "neo4j", executor=executor)
    assert executor.seen == []


def test_rate_one_profiles_and_prefixes_the_query(monkeypatch):
    monkeypatch.setenv("SEOCHO_PLAN_PROFILE_SAMPLE", "1")
    executor = _Executor()
    _engine()._record_plan_quality("MATCH (n) RETURN n", {}, "neo4j", executor=executor)
    assert executor.seen == ["PROFILE MATCH (n) RETURN n"]


def test_a_profiling_failure_never_propagates(monkeypatch):
    """A telemetry path must not be able to fail a user's query."""
    monkeypatch.setenv("SEOCHO_PLAN_PROFILE_SAMPLE", "1")

    class _Boom:
        def execute(self, plan):
            raise RuntimeError("PROFILE unsupported on this backend")

    _engine()._record_plan_quality("MATCH (n) RETURN n", {}, "neo4j", executor=_Boom())


def test_the_emitted_names_are_declared_instruments():
    from seocho.metrics import METRIC_SPECS

    for name in ("seocho.query.plan.count", "seocho.query.db_hits.count",
                 "seocho.query.scan.count", "seocho.query.plan_route.count"):
        assert name in METRIC_SPECS, name
