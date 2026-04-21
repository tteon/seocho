from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "benchmarks" / "run_cognee_finder_benchmark.py"
)
SPEC = importlib.util.spec_from_file_location("cognee_finder_benchmark_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_dataset_path_exists():
    assert MODULE._default_dataset_path().exists()


def test_extract_answer_prefers_first_search_result_like_object():
    class Result:
        def __init__(self, search_result: str) -> None:
            self.search_result = search_result

    answer = MODULE._extract_answer([Result("Alpha answer"), Result("Beta answer")])

    assert answer == "Alpha answer"


def test_extract_answer_handles_plain_string_lists():
    assert MODULE._extract_answer(["Alpha", "Beta"]) == "Alpha"


def test_remember_latency_prefers_elapsed_seconds():
    class RememberResult:
        elapsed_seconds = 12.345

    assert MODULE._remember_latency_ms(RememberResult(), 987.0) == 12345.0


def test_graph_counts_returns_zero_when_graph_file_missing():
    assert MODULE._graph_counts("/tmp/does-not-exist-cognee-graph-kuzu") == (0, 0)


def test_configure_cognee_environment_sets_embedding_model_and_skip_flag(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    roots = {
        "data_root": "/tmp/cognee-data",
        "system_root": "/tmp/cognee-system",
    }

    MODULE._configure_cognee_environment(
        roots,
        "gpt-4o-mini",
        embedding_model="text-embedding-3-large",
        skip_connection_test=True,
    )

    assert os.environ["EMBEDDING_MODEL"] == "text-embedding-3-large"
    assert os.environ["COGNEE_SKIP_CONNECTION_TEST"] == "true"
