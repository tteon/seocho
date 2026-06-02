"""Regression tests for seocho-hnf9 — _run_sync timeout + ask_stream cleanup.

Covers:
1. _run_sync(coro, timeout=N) raises asyncio.TimeoutError when the worker
   doesn't complete in time.
2. _run_sync without timeout still works (back-compat).
3. _run_sync propagates exceptions from the inner coroutine unchanged.
"""

from __future__ import annotations

import asyncio
import time

import pytest


def test_run_sync_no_timeout_completes_normally() -> None:
    """Default behaviour (no timeout) is unchanged — completes when coro completes."""
    from seocho.session import _run_sync

    async def _quick() -> int:
        return 42

    assert _run_sync(_quick()) == 42


def test_run_sync_timeout_raises_when_coro_too_slow() -> None:
    """timeout=0.05s on a 1s coroutine raises asyncio.TimeoutError."""
    from seocho.session import _run_sync

    async def _outer() -> int:
        async def _slow() -> int:
            await asyncio.sleep(1.0)
            return 99
        # Calling _run_sync from inside an awaited context forces the
        # worker-thread path (because there's already a running loop).
        return _run_sync(_slow(), timeout=0.05)

    with pytest.raises(asyncio.TimeoutError, match="did not complete within"):
        asyncio.run(_outer())


def test_run_sync_timeout_skipped_when_coro_fast() -> None:
    """timeout=2s with a fast coro still returns the value."""
    from seocho.session import _run_sync

    async def _outer() -> int:
        async def _fast() -> int:
            return 7
        return _run_sync(_fast(), timeout=2.0)

    assert asyncio.run(_outer()) == 7


def test_run_sync_propagates_inner_exception_via_worker() -> None:
    """Exceptions inside the worker coroutine surface unchanged."""
    from seocho.session import _run_sync

    async def _outer() -> None:
        async def _raise() -> None:
            raise ValueError("inner boom")
        _run_sync(_raise())

    with pytest.raises(ValueError, match="inner boom"):
        asyncio.run(_outer())
