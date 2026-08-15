"""WP0 simulator contract: chaining, exact-LRU MRC, both arrival orders."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "cache_simulator",
    Path(__file__).resolve().parents[2] / "scripts" / "cache_sim" / "simulator.py",
)
sim = importlib.util.module_from_spec(_spec)
sys.modules["cache_simulator"] = sim
_spec.loader.exec_module(sim)


def test_shared_prefix_shares_block_hashes_until_divergence():
    a = list(range(64))
    b = list(range(48)) + [999] * 16
    blocks_a = sim.blockify(a, block_size=16)
    blocks_b = sim.blockify(b, block_size=16)
    assert blocks_a[:3] == blocks_b[:3]      # identical first 48 tokens
    assert blocks_a[3] != blocks_b[3]        # divergence breaks the chain


def test_one_token_prefix_change_invalidates_every_downstream_block():
    a = list(range(64))
    b = [999] + list(range(1, 64))
    assert not set(sim.blockify(a, 16)) & set(sim.blockify(b, 16))


def test_partial_trailing_block_is_not_cacheable():
    assert len(sim.blockify(list(range(70)), block_size=16)) == 4  # 70 // 16


def test_mattson_exact_lru_hit_rates():
    # Stream A B C A B C: second round hits iff capacity >= 3 unique blocks.
    stream = ["A", "B", "C", "A", "B", "C"]
    result = sim.mattson_mrc(stream, capacities=[1, 2, 3])
    assert result.hit_rate_at(2) == 0.0
    assert result.hit_rate_at(3) == 0.5
    assert result.distance_histogram[-1] == 3  # three cold misses


def test_repeated_identical_context_is_all_hits_beyond_cold():
    context = list(range(160))
    report = sim.sweep([context] * 5, block_size=16)
    top = report.as_given.hit_rate_at(report.as_given.unique_blocks)
    assert top == 0.8  # 4 of 5 passes hit every block; first pass is cold
    assert report.shared_prefix_blocks == 10


def test_sweep_reports_both_arrival_orders_deterministically():
    contexts = [list(range(start, start + 96)) for start in range(0, 800, 8)]
    report_1 = sim.sweep(contexts, block_size=16, shuffle_seed=7)
    report_2 = sim.sweep(contexts, block_size=16, shuffle_seed=7)
    assert report_1.shuffled.to_dict() == report_2.shuffled.to_dict()
    assert report_1.as_given.accesses == report_1.shuffled.accesses


def test_chars_tokenizer_is_deterministic():
    assert sim.chars_tokenizer("seocho ontology") == sim.chars_tokenizer("seocho ontology")
    assert sim.chars_tokenizer("abcd") != sim.chars_tokenizer("abce")
