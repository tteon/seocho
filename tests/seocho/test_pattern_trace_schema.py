"""Trace schema v1 contract: version stamping, pattern vocabulary, refusal to mix."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "pattern_trace_schema",
    Path(__file__).resolve().parents[2] / "scripts" / "pattern_traces" / "schema.py",
)
schema = importlib.util.module_from_spec(_spec)
sys.modules["pattern_trace_schema"] = schema
_spec.loader.exec_module(schema)


def test_episode_round_trip(tmp_path):
    episode = schema.Episode(pattern="text2cypher", case_id="c1", model="m")
    episode.steps.append(schema.LLMStep(role="generate", model="m", parse="salvaged"))
    episode.steps.append(schema.ToolStep(name="graph_query", rows=3, node_ids=["a"]))
    episode.outcome = {"ok": True}
    out = tmp_path / "t.jsonl"
    schema.append_episode(out, episode)
    records = schema.read_episodes(out)
    assert records[0]["trace_schema"] == schema.TRACE_SCHEMA_VERSION
    assert records[0]["steps"][0]["parse"] == "salvaged"
    assert records[0]["steps"][1]["kind"] == "tool"


def test_unknown_pattern_refused():
    episode = schema.Episode(pattern="vibes", case_id="c", model="m")
    with pytest.raises(ValueError, match="unknown pattern"):
        episode.to_dict()


def test_schema_generation_mixing_refused(tmp_path):
    out = tmp_path / "t.jsonl"
    out.write_text('{"trace_schema": 99}\n')
    with pytest.raises(ValueError, match="refusing to silently mix"):
        schema.read_episodes(out)
