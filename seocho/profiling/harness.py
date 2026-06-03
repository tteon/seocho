"""Measurement primitives for the §21 native-acceleration gate.

Lifted from the hand-rolled logic in `scripts/profiling/bench_seocho_core.py`
so DISCOVERY and ADJUDICATION share one honest implementation. Everything here
is deterministic, $0 (no API), and reports `min` as the headline (the least-
interference estimate of true cost; Gregg, *Systems Performance*).

Honesty locks this module provides:
  - `no_external_network()` — any outbound (non-loopback) socket connect raises,
    so a profiling run can never silently spend the user's API budget.
  - `determinism()` — asserts byte-identical output across repeated runs; this is
    the check that catches FFI footguns like a Rust HashMap tie-break depending
    on per-process RandomState (ADR-0101).
"""
from __future__ import annotations

import gc
import json
import socket
import time
from contextlib import contextmanager
from typing import Any, Callable, List, NamedTuple, Optional


class Sample(NamedTuple):
    min_s: float
    median_s: float
    p90_s: float
    n: int
    warmup: int


class MarshalSplit(NamedTuple):
    total_s: float
    marshal_floor_s: float
    native_compute_s: float
    marshal_pct: float


class ParityResult(NamedTuple):
    ok: bool
    n_diffs: int
    sample_diffs: List[Any]


class DeterminismResult(NamedTuple):
    ok: bool
    n_runs: int
    n_distinct: int


def timed(fn: Callable[[], Any], *, n: int, warmup: int = 200, gc_off: bool = True) -> Sample:
    """Per-call (min, median, p90) seconds over `n` calls, warmed, GC optionally off.

    `min` is the headline: the sample least perturbed by scheduler/thermal noise.
    Warmup discards CPU-frequency ramp, page faults, allocator/attr-cache warmup.
    """
    for _ in range(warmup):
        fn()
    samples: List[int] = []
    if gc_off:
        gc.disable()
    try:
        for _ in range(n):
            t = time.perf_counter_ns()
            fn()
            samples.append(time.perf_counter_ns() - t)
    finally:
        if gc_off:
            gc.enable()
    samples.sort()
    m = len(samples)
    return Sample(
        min_s=samples[0] / 1e9,
        median_s=samples[m // 2] / 1e9,
        p90_s=samples[min(int(m * 0.9), m - 1)] / 1e9,
        n=n,
        warmup=warmup,
    )


def marshaling_split(native_fn: Callable[[], Any], noop_fn: Callable[[], Any], *, n: int,
                     warmup: int = 200) -> MarshalSplit:
    """Attribute a PyO3 call into boundary-marshaling vs native compute.

    `noop_fn` must pay the SAME boundary cost (same args marshaled) but do ~no
    work. native_compute = max(total - noop, 0). When marshal_pct is high the
    "speedup" is an illusion of crossing the boundary, not of computing faster
    (ADR-0101: the seocho-core cosine was 69% marshaling).
    """
    total = timed(native_fn, n=n, warmup=warmup).min_s
    floor = timed(noop_fn, n=n, warmup=warmup).min_s
    compute = max(total - floor, 0.0)
    return MarshalSplit(
        total_s=total,
        marshal_floor_s=floor,
        native_compute_s=compute,
        marshal_pct=(floor / total) if total > 0 else 0.0,
    )


def _canon(x: Any, key: Optional[Callable[[Any], Any]]) -> str:
    if key is not None:
        x = key(x)
    return json.dumps(x, sort_keys=True, default=str)


def parity(a: Any, b: Any, *, key: Optional[Callable[[Any], Any]] = None,
           max_samples: int = 5) -> ParityResult:
    """Are two outputs byte-identical after deterministic canonicalization?

    For collections, pass `key` to canonicalize each element into a sortable
    form. Returns the diff count and a few sample diffs for triage.
    """
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        ca = sorted(_canon(x, key) for x in a)
        cb = sorted(_canon(x, key) for x in b)
        sa, sb = set(ca), set(cb)
        diffs = list((sa ^ sb))
        return ParityResult(ok=not diffs, n_diffs=len(diffs), sample_diffs=diffs[:max_samples])
    ok = _canon(a, key) == _canon(b, key)
    return ParityResult(ok=ok, n_diffs=0 if ok else 1, sample_diffs=[] if ok else [a, b])


def determinism(fn: Callable[[], Any], *, runs: int = 5,
                key: Optional[Callable[[Any], Any]] = None) -> DeterminismResult:
    """Run `fn` `runs` times; assert byte-identical output every time.

    Catches non-determinism (e.g. hash-seed-dependent ordering) that a single
    run hides. A native path that fails this MUST NOT be activated (§21.5).
    """
    outs = set()
    for _ in range(runs):
        out = fn()
        if isinstance(out, (list, tuple)):
            outs.add(tuple(sorted(_canon(x, key) for x in out)))
        else:
            outs.add(_canon(out, key))
    return DeterminismResult(ok=len(outs) == 1, n_runs=runs, n_distinct=len(outs))


@contextmanager
def no_external_network():
    """Block any outbound (non-loopback) socket connect for the duration.

    Loopback (DozerDB at bolt://localhost, local files) is allowed; public hosts
    (api.openai.com, embedding endpoints, MARA) raise. This makes a profiling run
    that accidentally calls a paid API fail LOUDLY instead of silently spending
    the user's budget (CLAUDE.md §21.3).
    """
    _loopback = ("127.", "::1", "localhost")
    real_connect = socket.socket.connect

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        host = ""
        if isinstance(address, tuple) and address:
            host = str(address[0])
        if not (host.startswith(_loopback) or host == "::1"):
            raise RuntimeError(
                f"$0 profiling: external network connect to {host!r} blocked "
                f"(no_external_network). Profiling must not spend API budget."
            )
        return real_connect(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[assignment]
