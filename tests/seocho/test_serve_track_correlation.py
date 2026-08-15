"""Serve-track KV correlation contract: window discipline and honest attribution."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"serve_track_{name}", _ROOT / "scripts" / "serve_track" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kv_windows = _load("kv_windows")
correlate_kv = _load("correlate_kv")


def test_window_closes_even_when_the_call_raises(tmp_path):
    """A failed call still consumed cache; dropping it would bias attribution."""
    recorder = kv_windows.WindowRecorder(tmp_path / "kv_windows.jsonl")
    with pytest.raises(RuntimeError, match="boom"):
        with recorder.record_step(
            trace_id="t1", role="synthesize", model="m", provider="vllm"
        ):
            raise RuntimeError("boom")

    records = kv_windows.read_windows(tmp_path / "kv_windows.jsonl")
    assert len(records) == 1
    assert records[0]["t_end"] > 0.0


def test_overlapping_windows_refused(tmp_path):
    """Concurrency would interleave two stages' blocks inside one window."""
    recorder = kv_windows.WindowRecorder(tmp_path / "kv_windows.jsonl")
    with recorder.record_step(
        trace_id="t1", role="synthesize", model="m", provider="vllm"
    ):
        with pytest.raises(RuntimeError, match="still open"):
            with recorder.record_step(
                trace_id="t1", role="text2cypher", model="m", provider="vllm"
            ):
                pass


def test_mixed_window_schema_refused(tmp_path):
    path = tmp_path / "kv_windows.jsonl"
    path.write_text('{"window_schema": 99}\n')
    with pytest.raises(ValueError, match="refusing to silently mix"):
        kv_windows.read_windows(path)


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_events_outside_every_window_are_reported_not_spread(tmp_path):
    """An unattributable frame must stay visible, not be smoothed into a stage."""
    _write(tmp_path / "kv_windows.jsonl", [
        {"trace_id": "t1", "step_index": 0, "role": "synthesize", "model": "m",
         "provider": "vllm", "t_start": 100.0, "t_end": 101.0, "window_schema": 1,
         "usage": {}, "prompt_chars": 0, "prompt_sections": {}},
    ])
    _write(tmp_path / "kv_events.jsonl", [
        {"_tag": "BlockStored", "block_hashes": [1, 2, 3], "_recv_ts": 100.5},
        {"_tag": "BlockStored", "block_hashes": [4], "_recv_ts": 500.0},
    ])

    report = correlate_kv.correlate(tmp_path)
    assert report["events_attributed"] == 1
    assert report["events_unattributed"] == 1
    assert report["stages"]["synthesize"]["blocks_stored"] == 3


def test_unreported_cached_tokens_is_none_not_zero(tmp_path):
    """MARA reports no cached_tokens below a length threshold; that is not a miss."""
    _write(tmp_path / "kv_windows.jsonl", [
        {"trace_id": "t1", "step_index": 0, "role": "route", "model": "m",
         "provider": "mara", "t_start": 1.0, "t_end": 2.0, "window_schema": 1,
         "usage": {"prompt_tokens": 120}, "prompt_chars": 0, "prompt_sections": {}},
    ])
    _write(tmp_path / "kv_events.jsonl", [])

    stage = correlate_kv.correlate(tmp_path)["stages"]["route"]
    assert stage["prompt_tokens"] == 120
    assert stage["cache_hit_rate"] is None


def test_stable_prefix_stops_at_the_first_varying_section(tmp_path):
    """Prefix reuse ends where the prompt stops being byte-stable."""
    common = {"trace_id": "t1", "model": "m", "provider": "vllm", "window_schema": 1,
              "usage": {}, "prompt_chars": 0}
    _write(tmp_path / "kv_windows.jsonl", [
        {**common, "step_index": 0, "role": "synthesize", "t_start": 1.0, "t_end": 2.0,
         "prompt_sections": {"system": 400, "ontology": 900, "subgraph": 1200}},
        {**common, "step_index": 1, "role": "synthesize", "t_start": 3.0, "t_end": 4.0,
         "prompt_sections": {"system": 400, "ontology": 900, "subgraph": 1500}},
    ])
    _write(tmp_path / "kv_events.jsonl", [])

    stage = correlate_kv.correlate(tmp_path)["stages"]["synthesize"]
    # system + ontology are stable; subgraph varies, so the prefix ends there.
    assert stage["stable_prefix_chars"] == 1300


def test_zero_blocks_on_a_block_emitting_run_is_full_reuse_not_unobserved(tmp_path):
    """The headline result is a stage that stored nothing — it must not read as null."""
    common = {"trace_id": "t1", "model": "m", "provider": "vllm", "window_schema": 1,
              "prompt_chars": 0, "prompt_sections": {}}
    _write(tmp_path / "kv_windows.jsonl", [
        {**common, "step_index": 0, "role": "cold", "t_start": 1.0, "t_end": 2.0,
         "usage": {"prompt_tokens": 800}},
        {**common, "step_index": 1, "role": "warm", "t_start": 3.0, "t_end": 4.0,
         "usage": {"prompt_tokens": 800}},
    ])
    _write(tmp_path / "kv_events.jsonl", [
        {"type": "BlockStored", "block_hashes": list(range(50)),
         "block_size": 16, "medium": "GPU", "_recv_ts": 1.5},
    ])

    stages = correlate_kv.correlate(tmp_path)["stages"]
    assert stages["cold"]["prefix_reuse_rate"] == 0.0
    assert stages["warm"]["prefix_reuse_rate"] == 1.0
    assert stages["cold"]["media"] == {"GPU": 50}


def test_api_only_run_reports_none_not_full_reuse(tmp_path):
    """With no block evidence anywhere, reuse is unknown — not 100%."""
    _write(tmp_path / "kv_windows.jsonl", [
        {"trace_id": "t1", "step_index": 0, "role": "route", "model": "m",
         "provider": "mara", "t_start": 1.0, "t_end": 2.0, "window_schema": 1,
         "usage": {"prompt_tokens": 120}, "prompt_chars": 0, "prompt_sections": {}},
    ])
    _write(tmp_path / "kv_events.jsonl", [])

    assert correlate_kv.correlate(tmp_path)["stages"]["route"]["prefix_reuse_rate"] is None
