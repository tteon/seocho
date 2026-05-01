"""Regression tests for ``seocho.session._run_sync``.

The agent / supervisor / streaming code paths used to call
``asyncio.run(...)`` directly. ``asyncio.run`` raises
``RuntimeError: cannot be called from a running event loop`` whenever
there is already an outer loop — which is the default condition inside
Jupyter cells, FastAPI request handlers, and ``pytest-asyncio`` fixtures.
That made agent mode silently fall back to pipeline for every interactive
seocho consumer.

``_run_sync`` was added so the synchronous code path works regardless of
whether the caller is inside a running loop. These tests pin both branches
of the helper.
"""

from __future__ import annotations

import asyncio

import pytest

from seocho.session import _run_sync


def test_run_sync_in_plain_context_returns_value() -> None:
    """No outer loop: ``_run_sync`` delegates to ``asyncio.run`` and returns the awaited value."""

    async def _coro() -> int:
        return 42

    assert _run_sync(_coro()) == 42


def test_run_sync_propagates_exceptions_from_plain_context() -> None:
    """Exceptions inside the coroutine surface to the caller."""

    async def _coro() -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _run_sync(_coro())


def test_run_sync_works_inside_running_event_loop() -> None:
    """Outer loop is running: ``_run_sync`` must still complete without raising.

    This is the regression that broke Jupyter / FastAPI consumers — the
    classic ``RuntimeError: asyncio.run() cannot be called from a running
    event loop`` message. The helper detects the outer loop and runs the
    coroutine on a dedicated worker thread.
    """

    async def _outer() -> int:
        async def _inner() -> int:
            return 7

        # Calling _run_sync from inside an awaited coroutine — i.e. from a
        # context where asyncio.get_running_loop() returns the active loop.
        return _run_sync(_inner())

    assert asyncio.run(_outer()) == 7


def test_run_sync_propagates_exceptions_from_nested_loop() -> None:
    """Exceptions raised inside the worker thread surface to the caller."""

    async def _outer() -> None:
        async def _inner() -> None:
            raise RuntimeError("nested boom")

        _run_sync(_inner())

    with pytest.raises(RuntimeError, match="nested boom"):
        asyncio.run(_outer())
