"""Regression tests for ``seocho.session._stream_async_in_thread``.

``ask_stream`` used to drive its async generator with ``asyncio.new_event_loop()``
+ ``loop.run_until_complete(...)``, which raises ``RuntimeError`` whenever an outer
loop is already running — the default inside Jupyter cells, FastAPI handlers, and
``pytest-asyncio`` fixtures. The helper drives the generator on a worker thread
that owns its own loop, so streaming is safe from any context while still
delivering chunks in real time. These tests pin both branches.
"""

from __future__ import annotations

import asyncio

import pytest

from seocho.session import _stream_async_in_thread


def _counter_factory(n: int):
    async def _agen():
        for i in range(n):
            yield i

    return _agen


def test_stream_in_plain_context_yields_all_items() -> None:
    assert list(_stream_async_in_thread(_counter_factory(3))) == [0, 1, 2]


def test_stream_works_inside_running_event_loop() -> None:
    """The regression: streaming from inside a running loop must not raise."""

    async def _outer():
        # asyncio.get_running_loop() is active here — the case that used to crash.
        return list(_stream_async_in_thread(_counter_factory(3)))

    assert asyncio.run(_outer()) == [0, 1, 2]


def test_stream_propagates_exceptions_after_partial_output() -> None:
    def _boom_factory():
        async def _agen():
            yield 1
            raise ValueError("stream boom")

        return _agen()

    gen = _stream_async_in_thread(_boom_factory)
    assert next(gen) == 1
    with pytest.raises(ValueError, match="stream boom"):
        next(gen)
