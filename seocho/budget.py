"""
Per-Session and per-workspace token budget gates.

Closes seocho-oilg. CLAUDE.md §18 specifies the middleware ordering
``Validation → Policy → Cache → Budget → Retry → Observability``. The
*Budget* gate is implemented here.

A ``TokenBudgetTracker`` accumulates token spend reported by Sessions
or LLM responses, and raises :class:`BudgetExceededError` when a
configured ceiling is reached. Callers integrate it by:

1. Constructing a tracker (per-session, per-workspace, or shared).
2. Calling ``tracker.charge(prompt_tokens, completion_tokens)`` after
   each LLM call.
3. The tracker raises before the next call if the cumulative cost
   would exceed the budget.

This module is intentionally storage-agnostic — a tracker holds counts
in-memory; production deployments wanting cross-process aggregation
should subclass and back the counts with Redis or similar.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional


class BudgetExceededError(RuntimeError):
    """Raised when a token budget is exhausted.

    Carries ``spent``, ``budget``, and ``scope`` attributes so callers
    can format meaningful error messages or HTTP 402/429 responses.
    """

    def __init__(self, *, scope: str, spent: int, budget: int) -> None:
        super().__init__(
            f"Token budget exceeded for {scope!r}: spent={spent} budget={budget}"
        )
        self.scope = scope
        self.spent = spent
        self.budget = budget


@dataclass
class TokenBudgetTracker:
    """Thread-safe accumulator for per-scope token spend.

    Parameters
    ----------
    budget:
        Maximum tokens allowed before :class:`BudgetExceededError` fires.
        ``0`` means unlimited (no enforcement) — back-compat default.
    scope:
        Free-form label used in error messages (e.g. ``"session-12ab"``,
        ``"workspace=acme"``).

    Attributes
    ----------
    prompt_tokens / completion_tokens:
        Cumulative counts. Reading is thread-safe; readers see a
        consistent snapshot via the internal lock.
    """

    budget: int = 0  # 0 = unlimited
    scope: str = "default"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total(self) -> int:
        with self._lock:
            return self.prompt_tokens + self.completion_tokens

    def remaining(self) -> int:
        """Tokens left before the budget is exhausted; -1 if unlimited."""
        with self._lock:
            if self.budget <= 0:
                return -1
            return max(0, self.budget - (self.prompt_tokens + self.completion_tokens))

    def charge(self, prompt: int = 0, completion: int = 0) -> None:
        """Add to the cumulative cost. Raises if the new total exceeds budget."""
        if prompt < 0 or completion < 0:
            raise ValueError("charge counts must be non-negative")
        with self._lock:
            self.prompt_tokens += int(prompt)
            self.completion_tokens += int(completion)
            total = self.prompt_tokens + self.completion_tokens
            if self.budget > 0 and total > self.budget:
                raise BudgetExceededError(
                    scope=self.scope, spent=total, budget=self.budget
                )

    def check(self, anticipated: int = 0) -> None:
        """Raise BudgetExceededError if (current + anticipated) exceeds budget.

        Useful as a pre-flight check before launching an expensive call —
        cheaper than failing after the LLM round-trip.
        """
        with self._lock:
            total = self.prompt_tokens + self.completion_tokens + max(0, int(anticipated))
            if self.budget > 0 and total > self.budget:
                raise BudgetExceededError(
                    scope=self.scope, spent=total, budget=self.budget
                )

    def reset(self) -> None:
        """Zero the counters. Budget value is preserved."""
        with self._lock:
            self.prompt_tokens = 0
            self.completion_tokens = 0

    def snapshot(self) -> Dict[str, int]:
        """Return a thread-safe view of the current state."""
        with self._lock:
            return {
                "scope": self.scope,
                "budget": self.budget,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total": self.prompt_tokens + self.completion_tokens,
                "remaining": (
                    -1
                    if self.budget <= 0
                    else max(
                        0,
                        self.budget - (self.prompt_tokens + self.completion_tokens),
                    )
                ),
            }


# Module-level registry for shared trackers (e.g. per-workspace).
_REGISTRY: Dict[str, TokenBudgetTracker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_budget(scope: str, *, budget: int = 0) -> TokenBudgetTracker:
    """Get or create a shared :class:`TokenBudgetTracker` for *scope*.

    Subsequent calls with the same scope return the same tracker. The
    ``budget`` argument is honoured only on first creation; callers
    that need to update an existing tracker should mutate
    ``tracker.budget`` directly.
    """
    with _REGISTRY_LOCK:
        if scope not in _REGISTRY:
            _REGISTRY[scope] = TokenBudgetTracker(budget=budget, scope=scope)
        return _REGISTRY[scope]


def clear_budgets() -> None:
    """Drop all shared trackers — primarily for tests."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
