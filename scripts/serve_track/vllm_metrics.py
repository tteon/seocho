"""Scrape the KV-relevant slice of vLLM's Prometheus endpoint.

The KV-event stream gives block *identity* (hashes, parent chain, medium) but no
volume or timing. `/metrics` gives volume and timing but is a set of process-wide
counters with no notion of a request, let alone a RAG stage. Sampling it at the
boundaries of a `WindowRecorder` window and differencing turns the counters into
per-stage numbers, which is the only way to say "the synthesize stage moved N
bytes across PCIe and spent M ms doing it".

Metric names verified against the installed vLLM 0.27.1, not the docs:

  transfer volume/time (Counter)
    vllm:kv_offload_store_bytes   GPU -> CPU bytes
    vllm:kv_offload_load_bytes    CPU -> GPU bytes
    vllm:kv_offload_store_time    seconds spent storing
    vllm:kv_offload_load_time     seconds spent loading
  lookup cost (Histogram)
    vllm:kv_offload_lookup_sync_delay_seconds
    vllm:kv_offload_lookup_async_delay_seconds
  pressure (Counter/Gauge)
    vllm:kv_offload_allocation_failure
    vllm:kv_offload_cpu_cache_usage_perc
  hit rate
    vllm:prefix_cache_hits / vllm:prefix_cache_queries            (GPU tier)
    vllm:external_prefix_cache_hits / _queries                    (offloaded tier)
  occupancy
    vllm:kv_cache_usage_perc

Deliberately NOT used: ``vllm:kv_offload_total_bytes``, ``_total_time`` and
``_size``. They are marked deprecated in 0.27.1
(`kv_connector/v1/offloading/metrics.py`, `_DEPRECATED_*`), superseded by the
store/load split — which is the more useful shape anyway, since offloading is
only worth it when loads (recall) outweigh stores (spill).
"""

from __future__ import annotations

from typing import Dict, Optional
from urllib.error import URLError
from urllib.request import urlopen

# Counters and gauges worth differencing across a window. Histograms are
# excluded: differencing a bucket set does not yield a meaningful per-window
# distribution, and their `_sum`/`_count` are captured below instead.
_SCALAR_METRICS = (
    "vllm:kv_offload_store_bytes",
    "vllm:kv_offload_load_bytes",
    "vllm:kv_offload_store_time",
    "vllm:kv_offload_load_time",
    "vllm:kv_offload_allocation_failure",
    "vllm:kv_offload_stores_skipped",
    "vllm:kv_offload_cpu_cache_usage_perc",
    "vllm:prefix_cache_hits",
    "vllm:prefix_cache_queries",
    "vllm:external_prefix_cache_hits",
    "vllm:external_prefix_cache_queries",
    "vllm:kv_cache_usage_perc",
)

# Histogram aggregates: `_sum` over `_count` is the mean, which does survive
# differencing across a window.
_HISTOGRAM_METRICS = (
    "vllm:kv_offload_lookup_sync_delay_seconds",
    "vllm:kv_offload_lookup_async_delay_seconds",
)


def _wanted(name: str) -> bool:
    if name in _SCALAR_METRICS:
        return True
    for base in _HISTOGRAM_METRICS:
        if name in (f"{base}_sum", f"{base}_count"):
            return True
    # Prometheus client suffixes counters with _total.
    return any(name == f"{m}_total" for m in _SCALAR_METRICS)


def parse_metrics(text: str) -> Dict[str, float]:
    """Parse the exposition format, summing across label sets.

    vLLM labels most metrics per engine and per model. A serve-track run has one
    engine, and summing keeps the reader honest if that ever stops being true —
    a silently-dropped label set would understate transfer volume.
    """
    values: Dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, raw_value = line.rpartition(" ")
        if not head:
            continue
        name = head.split("{", 1)[0]
        if not _wanted(name):
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        values[name] = values.get(name, 0.0) + value
    return values


def sample(url: str, timeout: float = 2.0) -> Dict[str, float]:
    """Snapshot the endpoint; an unreachable server yields no sample, not a crash.

    Instrumentation must not be able to fail a run, and a missing sample is
    visible downstream as an absent `metrics_delta` rather than as zeros that
    would read as "no bytes moved".
    """
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - operator-supplied
            return parse_metrics(response.read().decode("utf-8", "replace"))
    except (URLError, OSError, ValueError):
        return {}


def delta(before: Dict[str, float], after: Dict[str, float]) -> Dict[str, float]:
    """Per-window change. Gauges are carried as their end value, not a difference.

    `*_usage_perc` is an occupancy gauge: its difference over a window is
    meaningless, while its value at window close describes the state the next
    stage inherits.
    """
    out: Dict[str, float] = {}
    if not before or not after:
        return out
    for name, end in after.items():
        if name.endswith("_usage_perc"):
            out[name] = round(end, 6)
            continue
        change = end - before.get(name, 0.0)
        if change:
            out[name] = round(change, 6)
    return out


def make_sampler(url: Optional[str]):
    """Return a zero-argument sampler, or None when no endpoint was given."""
    if not url:
        return None
    return lambda: sample(url)
