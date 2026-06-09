"""Offline test for the synergy-#1 aggregation (seocho-jdg / seocho-9xo).

The live latency run needs DozerDB + MARA; this pins the pure metric math:
cross-session cache-hit-rate and the cold-vs-warm p99 ratio.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "finder_cache_synergy", ROOT / "scripts" / "benchmarks" / "finder_cache_synergy.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
summarize_cache_run = mod.summarize_cache_run


def test_all_warm_hits_and_huge_latency_win():
    # cold: real compute latencies; warm: persistent-cache hits (sub-ms)
    cold = [(546.0, "pipeline"), (641.0, "pipeline"), (650.0, "pipeline"),
            (586.0, "pipeline"), (1088.0, "pipeline")]
    warm = [(0.3, "cache_persistent")] * 5
    s = summarize_cache_run(cold, warm)
    assert s["cache_hit_rate"] == 1.0
    assert s["cold_p99_ms"] == 1088.0
    assert s["warm_p99_ms"] == 0.3
    assert s["warm_over_cold_p99_ratio"] < 0.5
    assert s["meets_0_5x_target"] is True


def test_partial_hits_counted():
    cold = [(500.0, "pipeline"), (500.0, "pipeline")]
    warm = [(0.2, "cache_persistent"), (480.0, "pipeline")]  # one miss (recomputed)
    s = summarize_cache_run(cold, warm)
    assert s["cache_hit_rate"] == 0.5


def test_no_win_when_warm_not_faster():
    # degenerate: warm not served from cache -> ratio ~1, target not met
    cold = [(500.0, "pipeline")]
    warm = [(500.0, "pipeline")]
    s = summarize_cache_run(cold, warm)
    assert s["cache_hit_rate"] == 0.0
    assert s["meets_0_5x_target"] is False
