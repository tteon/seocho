"""Test-wide defaults for the SEOCHO SDK suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _strict_metrics():
    """Make metric validation raise during tests.

    In production a validation failure is swallowed: the emit calls in
    `store/llm.py` sit inside the same `try` as the provider request, whose
    `except Exception` is the retry handler, so a raising telemetry call turned
    a successful completion into a retried "LLM failure" at real cost.

    But swallowing by default would also make the validator's contract
    unenforceable — a test asserting that a forbidden attribute is rejected
    would pass whether or not the rejection happened. Tests therefore opt into
    strict mode, so the guard protects production without hiding regressions
    from us.
    """
    from seocho.metrics import get_metrics

    metrics = get_metrics()
    previous = getattr(metrics, "strict", False)
    metrics.strict = True
    try:
        yield
    finally:
        metrics.strict = previous
