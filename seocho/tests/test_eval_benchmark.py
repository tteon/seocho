"""Regression tests for seocho-foq7 — eval framework umbrella."""

from __future__ import annotations

import json

import pytest


def test_corpus_immutable_from_runner_view() -> None:
    """BenchmarkCorpus copies its inputs so callers can't mutate them later."""
    from seocho.eval import BenchmarkCorpus
    docs = ["a", "b"]
    qs = ["q?"]
    corpus = BenchmarkCorpus(name="t", documents=docs, queries=qs)
    docs.append("evil")
    qs.append("hack?")
    assert corpus.documents == ["a", "b"]
    assert corpus.queries == ["q?"]


def test_runner_emits_index_and_query_spans(tmp_path) -> None:
    from seocho.eval import BenchmarkCorpus, BenchmarkRunner

    corpus = BenchmarkCorpus(
        name="smoke",
        documents=["Tim Cook is the CEO of Apple."],
        queries=["Who runs Apple?"],
    )
    runner = BenchmarkRunner(
        config_label="cfg-a",
        workspace_id="ws-1",
        ontology_identity_hash="h1",
        user_id="alice",
        cache_prefix_hash="prefix-AAA",
        output_path=str(tmp_path / "spans.jsonl"),
    )

    def _index(text):
        return {"source_id": "src-1", "nodes_created": 1, "relationships_created": 0}

    def _query(question):
        return "Tim Cook"

    spans = runner.run(corpus, index_fn=_index, query_fn=_query)
    assert len(spans) == 2
    assert spans[0].operation == "index"
    assert spans[1].operation == "query"
    assert spans[1].output_preview == "Tim Cook"


def test_runner_writes_jsonl_artifact(tmp_path) -> None:
    from seocho.eval import BenchmarkCorpus, BenchmarkRunner, load_jsonl_spans

    path = tmp_path / "out.jsonl"
    corpus = BenchmarkCorpus(name="t", documents=["d1"], queries=[])
    runner = BenchmarkRunner(
        config_label="x",
        workspace_id="ws",
        ontology_identity_hash="h",
        output_path=str(path),
    )
    runner.run(corpus, index_fn=lambda t: {"source_id": "s"}, query_fn=lambda q: "")
    loaded = load_jsonl_spans(str(path))
    assert len(loaded) == 1
    assert loaded[0]["operation"] == "index"


def test_runner_captures_degraded_flag(tmp_path) -> None:
    from seocho.eval import BenchmarkCorpus, BenchmarkRunner

    corpus = BenchmarkCorpus(name="t", documents=["d"], queries=[])
    runner = BenchmarkRunner(
        config_label="x", workspace_id="w", ontology_identity_hash="h",
    )
    runner.run(
        corpus,
        index_fn=lambda t: {
            "source_id": "s",
            "degraded": True,
            "fallback_from": "agent",
            "degraded_observability": True,
        },
        query_fn=lambda q: "",
    )
    span = runner.spans[0]
    assert span.degraded is True
    assert span.fallback_from == "agent"
    assert span.degraded_observability is True


def test_compute_run_summary_groups_by_config_label() -> None:
    from seocho.eval import compute_run_summary
    spans = [
        {"config_label": "a", "stage_timings": {"total_seconds": 0.1},
         "degraded": False, "degraded_observability": False,
         "prompt_tokens": 100, "completion_tokens": 50, "cache_prefix_hash": "x"},
        {"config_label": "a", "stage_timings": {"total_seconds": 0.2},
         "degraded": True,  "degraded_observability": False,
         "prompt_tokens": 100, "completion_tokens": 50, "cache_prefix_hash": "x"},
        {"config_label": "b", "stage_timings": {"total_seconds": 0.5},
         "degraded": False, "degraded_observability": False,
         "prompt_tokens": 200, "completion_tokens": 100, "cache_prefix_hash": "y"},
    ]
    s = compute_run_summary(spans)
    assert set(s.keys()) == {"a", "b"}
    assert s["a"]["count"] == 2
    assert abs(s["a"]["degraded_rate"] - 0.5) < 1e-9
    assert s["a"]["total_prompt_tokens"] == 200
    assert s["b"]["count"] == 1
    assert s["b"]["latency_mean"] == 0.5


def test_compute_run_summary_estimates_cache_hit_ratio() -> None:
    """Same cache_prefix_hash on subsequent spans → hit ratio > 0."""
    from seocho.eval import compute_run_summary
    spans = [
        {"config_label": "x", "stage_timings": {"total_seconds": 1},
         "cache_prefix_hash": "P1", "degraded": False, "degraded_observability": False,
         "prompt_tokens": 0, "completion_tokens": 0},
        {"config_label": "x", "stage_timings": {"total_seconds": 1},
         "cache_prefix_hash": "P1", "degraded": False, "degraded_observability": False,
         "prompt_tokens": 0, "completion_tokens": 0},
        {"config_label": "x", "stage_timings": {"total_seconds": 1},
         "cache_prefix_hash": "P1", "degraded": False, "degraded_observability": False,
         "prompt_tokens": 0, "completion_tokens": 0},
    ]
    s = compute_run_summary(spans)
    # 2 of 3 are repeats of P1 → 2/3 hit ratio
    assert abs(s["x"]["prompt_cache_hit_ratio"] - 2/3) < 1e-9


def test_compute_run_summary_handles_empty_input() -> None:
    from seocho.eval import compute_run_summary
    assert compute_run_summary([]) == {}


def test_runner_handles_index_fn_exception(tmp_path) -> None:
    from seocho.eval import BenchmarkCorpus, BenchmarkRunner

    corpus = BenchmarkCorpus(name="t", documents=["d"], queries=[])
    runner = BenchmarkRunner(
        config_label="x", workspace_id="w", ontology_identity_hash="h",
    )

    def _broken(text):
        raise ValueError("simulated failure")

    runner.run(corpus, index_fn=_broken, query_fn=lambda q: "")
    span = runner.spans[0]
    assert span.degraded is True
    assert span.fallback_from == "exception"
