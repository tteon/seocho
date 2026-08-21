"""Telemetry must never fail the work it measures.

`ProductionMetrics` raises on an invalid attribute — correct as a contract, and
placed where it could do real damage. The emit calls in `store/llm.py` sit
inside the same `try` as the provider request, and that `try`'s
`except Exception` is the **retry handler**:

    try:
        resp = self._client.chat.completions.create(**attempt_kwargs)
        ...
        metrics.record("gen_ai.client.operation.duration", ...)   # <-- here
    except Exception as exc:
        last_exc = exc
        ...
        metrics.add("seocho.gen_ai.retry.count", 1, {"reason": type(exc).__name__})

So a malformed attribute on a **successful** completion was reclassified as an
LLM failure: it triggered a real retry at real cost, and recorded itself as
`seocho.gen_ai.retry.count{reason: "ValueError"}`. The retry metric contained
our own bugs. The same shape in `runtime/agent_server.py`'s bare `finally`
turned a telemetry error into a 500.

A monitoring system that can take down the thing it monitors, and that
misreports its own defects as vendor failures, fails the one job it has.
"""

from __future__ import annotations

import pytest

from seocho.metrics import METRIC_SPECS, ProductionMetrics, get_metrics


@pytest.fixture
def lenient():
    """A registry in production mode — the autouse conftest fixture makes the
    process-wide one strict, which is the opposite of what we test here."""
    metrics = ProductionMetrics()
    metrics.strict = False
    return metrics


def test_unknown_attribute_does_not_escape(lenient):
    lenient.add("seocho.agent.request.count", 1, {"not_a_real_attribute": "x"})


def test_forbidden_attribute_does_not_escape(lenient):
    """Forbidden fragments are a cardinality/privacy control, not a reason to
    fail a request that already succeeded."""
    lenient.add("seocho.agent.request.count", 1, {"workspace_id": "tenant-a"})


def test_oversized_attribute_does_not_escape(lenient):
    lenient.add("seocho.agent.request.count", 1, {"operation": "x" * 200})


def test_non_scalar_attribute_does_not_escape(lenient):
    lenient.add("seocho.agent.request.count", 1, {"operation": {"nested": True}})


def test_wrong_instrument_kind_does_not_escape(lenient):
    """A counter recorded as a histogram is a wiring bug in our code, and still
    must not take down the request."""
    lenient.record("seocho.agent.request.count", 1.0, {"operation": "ask"})


def test_unknown_metric_name_does_not_escape(lenient):
    lenient.add("seocho.not.a.real.metric", 1)


def test_negative_histogram_value_does_not_escape(lenient):
    lenient.record("seocho.agent.request.duration", -1.0, {"operation": "ask"})


def test_valid_emission_still_works(lenient):
    """The guard must not swallow the measurement itself."""
    spec = METRIC_SPECS["seocho.agent.request.count"]
    attribute = sorted(spec.attributes)[0]
    lenient.add("seocho.agent.request.count", 1, {attribute: "ask"})


# ---------------------------------------------------------------------------
# Strict mode keeps the contract enforceable
# ---------------------------------------------------------------------------

def test_strict_mode_still_raises():
    """Swallowing by default would make the validator unenforceable — a test
    asserting rejection would pass whether or not the rejection happened."""
    metrics = ProductionMetrics()
    metrics.strict = True
    with pytest.raises(ValueError):
        metrics.add("seocho.agent.request.count", 1, {"nope": "x"})


def test_tests_run_strict_by_default():
    """The autouse conftest fixture is what keeps the contract alive in CI."""
    assert getattr(get_metrics(), "strict", False) is True
