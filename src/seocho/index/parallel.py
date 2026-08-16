"""Concurrency primitive for indexing (seocho-ia4: indexing parallelism, step 1).

The dominant indexing cost is per-chunk LLM extraction/linking — embarrassingly
parallel across chunks, but **I/O-bound** (LLM API round-trips), so the win is
overlapping the network waits, not local compute. A thread pool over the sync LLM
client suffices (Rust/processes buy nothing for I/O-bound work). CPU-bound stages
(interning, dedup, mining) are a separate story — see ``shared_intern`` and the
profile ADR.

``concurrent_map`` is order-preserving and exception-capturing: it never reorders
results (the deterministic post-processing that follows depends on chunk order) and
never lets one chunk's failure abort the batch (each item's exception is returned in
place, so the caller applies its own per-item fallback — mirroring the pipeline's
strict/guided fallback contract).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Sequence, TypeVar, Union

T = TypeVar("T")
R = TypeVar("R")


def concurrent_map(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    max_workers: int = 1,
    capture_exceptions: bool = True,
) -> List[Union[R, Exception]]:
    """Apply ``fn`` to each item, up to ``max_workers`` at a time, order-preserved.

    - ``max_workers <= 1`` runs sequentially (zero overhead, exact back-compat).
    - With ``capture_exceptions`` (default) a failing item yields the ``Exception``
      object in its result slot instead of aborting the batch; the caller decides
      how to handle it (e.g. the pipeline's heuristic fallback).
    """
    n = len(items)
    if n == 0:
        return []
    if max_workers <= 1:
        out: List[Union[R, Exception]] = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as exc:  # noqa: BLE001
                if not capture_exceptions:
                    raise
                out.append(exc)
        return out

    results: List[Any] = [None] * n
    with ThreadPoolExecutor(max_workers=min(max_workers, n)) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for fut, i in list(futs.items()):
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                if not capture_exceptions:
                    raise
                results[i] = exc
    return results


def resolve_workers(requested: int, n_items: int, *, cap: int = 16) -> int:
    """Clamp a requested worker count to something sane for a batch of ``n_items``."""
    if requested <= 1:
        return 1
    return max(1, min(requested, n_items, cap))
