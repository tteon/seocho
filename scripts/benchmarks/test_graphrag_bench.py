"""Unit tests for graphrag_bench.py — no network, no real LLM."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import graphrag_bench as bench
from _preflight import local_llm_api_key_error


def test_smoke_dataset_shape():
    """The bundled smoke dataset has the expected row shape."""
    assert len(bench.SMOKE_DATASET) == 5
    for case in bench.SMOKE_DATASET:
        assert "corpus" in case
        assert "question" in case
        assert "answer" in case
        assert "gold_entities" in case
        assert isinstance(case["corpus"], list)
        assert case["corpus"]


def test_build_ontology_from_spec():
    onto = bench.build_ontology(bench.SMOKE_ONTOLOGY)
    assert onto.name == "benchmark_smoke"
    assert set(onto.nodes) == {"Person", "Company", "Country"}
    assert "WORKS_AT" in onto.relationships
    assert onto.relationships["WORKS_AT"].source == "Person"
    assert onto.relationships["WORKS_AT"].target == "Company"


def test_build_ontology_unique_property_detected():
    onto = bench.build_ontology(bench.SMOKE_ONTOLOGY)
    person_name = onto.nodes["Person"].properties["name"]
    assert person_name.unique is True


def test_aggregate_empty_returns_zeros():
    agg = bench.aggregate([])
    assert agg["exact_match"] == 0.0
    assert agg["substring_match"] == 0.0
    assert agg["latency_p50_ms"] == 0.0


def test_aggregate_computes_percentiles():
    cases = [
        bench.CaseResult(
            question="q1", gold_answer="a", predicted_answer="a",
            exact_match=True, substring_match=True, latency_ms=100.0,
            entity_recall=1.0,
        ),
        bench.CaseResult(
            question="q2", gold_answer="b", predicted_answer="wrong",
            exact_match=False, substring_match=False, latency_ms=200.0,
            entity_recall=0.5,
        ),
    ]
    agg = bench.aggregate(cases)
    assert agg["exact_match"] == 0.5
    assert agg["substring_match"] == 0.5
    assert agg["avg_entity_recall"] == 0.75
    assert agg["latency_p50_ms"] == 150.0


def test_aggregate_skips_errored_cases():
    cases = [
        bench.CaseResult(
            question="q1", gold_answer="a", predicted_answer="a",
            exact_match=True, substring_match=True, latency_ms=50.0,
        ),
        bench.CaseResult(
            question="q2", gold_answer="b", predicted_answer="",
            exact_match=False, substring_match=False, latency_ms=0.0,
            error="ingest_failed: boom",
        ),
    ]
    agg = bench.aggregate(cases)
    assert agg["exact_match"] == 1.0  # only the 1 successful case counts


def test_load_dataset_sample():
    onto_spec, rows = bench.load_dataset("sample", None)
    assert onto_spec["name"] == "benchmark_smoke"
    assert len(rows) == 5


def test_load_dataset_from_file(tmp_path):
    dataset_path = tmp_path / "custom.jsonl"
    dataset_path.write_text(
        json.dumps({
            "corpus": ["Alice works at Apple."],
            "question": "Where does Alice work?",
            "answer": "Apple",
            "gold_entities": ["Alice", "Apple"],
        }) + "\n",
        encoding="utf-8",
    )
    _, rows = bench.load_dataset("custom", str(dataset_path))
    assert len(rows) == 1
    assert rows[0]["answer"] == "Apple"


def test_benchmark_result_serializes_to_dict():
    result = bench.BenchmarkResult(
        task="sample",
        total_cases=1,
        completed_cases=1,
        exact_match=1.0,
        substring_match=1.0,
        avg_entity_recall=1.0,
        latency_p50_ms=100.0,
        latency_p95_ms=100.0,
    )
    d = result.to_dict()
    assert d["task"] == "sample"
    assert d["exact_match"] == 1.0
    assert "cases" in d


def test_local_llm_api_key_preflight_reports_missing_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    error = local_llm_api_key_error("openai/gpt-4o-mini")

    assert "OPENAI_API_KEY is required" in error
    assert "--api-key" in error


def test_local_llm_api_key_preflight_accepts_explicit_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert local_llm_api_key_error("openai/gpt-4o-mini", "sk-test") == ""
