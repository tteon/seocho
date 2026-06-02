"""Regression tests for seocho-oilg — TokenBudgetTracker.

Implements the *Budget* middleware gate from CLAUDE.md §18. The tracker
accumulates per-scope token spend and raises BudgetExceededError when
the configured ceiling is reached.
"""

from __future__ import annotations

import threading

import pytest


def test_unlimited_budget_never_raises() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=0)  # 0 = unlimited
    tracker.charge(prompt=1_000_000, completion=1_000_000)
    assert tracker.total == 2_000_000
    assert tracker.remaining() == -1


def test_budget_raises_when_exceeded() -> None:
    from seocho.budget import BudgetExceededError, TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100, scope="test")
    tracker.charge(prompt=40, completion=30)  # total=70, ok
    with pytest.raises(BudgetExceededError) as ei:
        tracker.charge(prompt=50)  # would be total=120
    assert ei.value.scope == "test"
    assert ei.value.budget == 100
    assert ei.value.spent == 120


def test_check_pre_flight_rejects_anticipated_overspend() -> None:
    from seocho.budget import BudgetExceededError, TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100, scope="t")
    tracker.charge(prompt=80)
    # tracker.check(anticipated) does not actually deduct, just inspects
    with pytest.raises(BudgetExceededError):
        tracker.check(anticipated=30)
    assert tracker.total == 80  # check did not modify state


def test_check_pre_flight_passes_under_budget() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100, scope="t")
    tracker.charge(prompt=50)
    tracker.check(anticipated=40)  # 90 total, OK
    assert tracker.total == 50


def test_reset_zeros_counters_keeps_budget() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100)
    tracker.charge(prompt=50)
    tracker.reset()
    assert tracker.total == 0
    assert tracker.budget == 100


def test_remaining_reflects_spend() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100)
    tracker.charge(prompt=30, completion=20)
    assert tracker.remaining() == 50


def test_snapshot_returns_dict() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100, scope="snap")
    tracker.charge(prompt=10, completion=20)
    snap = tracker.snapshot()
    assert snap == {
        "scope": "snap", "budget": 100,
        "prompt_tokens": 10, "completion_tokens": 20,
        "total": 30, "remaining": 70,
    }


def test_negative_charges_rejected() -> None:
    from seocho.budget import TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=100)
    with pytest.raises(ValueError):
        tracker.charge(prompt=-1)


def test_concurrent_charges_serialize_correctly() -> None:
    """Multiple threads charging the same tracker land all increments."""
    from seocho.budget import BudgetExceededError, TokenBudgetTracker
    tracker = TokenBudgetTracker(budget=10_000, scope="concurrent")
    errors = []

    def _worker():
        try:
            for _ in range(100):
                tracker.charge(prompt=1)
        except BudgetExceededError:
            errors.append("budget exceeded")

    threads = [threading.Thread(target=_worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert tracker.total == 1000  # 10 threads * 100 charges
    assert errors == []


def test_get_budget_registry_returns_same_instance() -> None:
    from seocho.budget import clear_budgets, get_budget
    clear_budgets()
    a = get_budget("workspace=acme", budget=500)
    b = get_budget("workspace=acme")
    assert a is b
    assert a.budget == 500


def test_get_budget_distinct_scopes_distinct_trackers() -> None:
    from seocho.budget import clear_budgets, get_budget
    clear_budgets()
    a = get_budget("workspace=acme")
    b = get_budget("workspace=beta")
    assert a is not b
