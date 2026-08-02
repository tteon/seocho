"""Run the parts of an experiment that do not depend on each other, at once.

Most measurements here are a loop over independent units — a database per
condition, a workspace per case, a category per read — and running them one at a
time wastes the box. This gives the two shapes that cover almost all of it, with
the choice between them made on what the work is bound by rather than by taste:

    threads    for anything waiting on a socket. Database reads and model calls
               hold the GIL for microseconds and wait for milliseconds, so
               threads are the right tool and processes would only add pickling.
    processes  for anything that burns CPU in Python. Embedding batches, hashing,
               n-gram counting over the corpus. Threads cannot help there.

Both keep results in the order the inputs came in, because a measurement that
silently reorders its rows is a bug waiting to be blamed on the data. Failures
are returned rather than raised, so one bad unit does not discard the other
fifteen, and the caller can count them (CLAUDE.md 20.2 — a failure is recorded,
never imputed).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def cpu_workers(cap: int = 16) -> int:
    """Leave a core for the interpreter and whatever else is running."""
    return max(1, min(cap, (os.cpu_count() or 2) - 1))


def io_map(fn: Callable[[T], R], items: Sequence[T], *,
           workers: int | None = None,
           on_error: Callable[[T, Exception], None] | None = None
           ) -> list[R | None]:
    """Thread-parallel map for socket-bound work. Order preserved.

    A unit that raises yields None in its slot and is passed to `on_error`, so
    the caller can report attempted against completed instead of quietly
    returning a shorter list.
    """
    if not items:
        return []
    count = workers or min(len(items), cpu_workers() * 2)
    results: list[R | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 — recorded, never imputed
                if on_error is not None:
                    on_error(items[index], exc)
    return results


def cpu_map(fn: Callable[[T], R], items: Sequence[T], *,
            workers: int | None = None,
            on_error: Callable[[T, Exception], None] | None = None
            ) -> list[R | None]:
    """Process-parallel map for CPU-bound work. Order preserved.

    `fn` must be importable at module level, which is the price of processes.
    A closure will fail to pickle, and that failure is loud rather than silent.
    """
    if not items:
        return []
    count = workers or min(len(items), cpu_workers())
    if count == 1:
        return [fn(item) for item in items]
    results: list[R | None] = [None] * len(items)
    with ProcessPoolExecutor(max_workers=count) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                if on_error is not None:
                    on_error(items[index], exc)
    return results


def chunked(items: Sequence[T], parts: int) -> list[list[T]]:
    """Split into roughly equal parts, for handing whole batches to a worker.

    Per-item process dispatch costs more than the work when items are small;
    sending a slice amortises it.
    """
    if parts <= 1 or not items:
        return [list(items)]
    size = max(1, (len(items) + parts - 1) // parts)
    return [list(items[i:i + size]) for i in range(0, len(items), size)]
