"""Regression coverage for saved-run evidence failures; no live services."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from seocho import e2e
from seocho.index.file_reader import FileIndexer, FileTracker
from seocho.index.pipeline import IndexingResult
from seocho.query.multi_plan import multi_plan_enabled
from seocho.run_comparison import compare_runs
from seocho.run_evidence import collect_evidence
from seocho.run_preflight import PreflightReport, _check_graph
from seocho.run_spec import RunSpec, parse_run_spec
from test_e2e_runner import _FakeClient, _fake_context, _write_fixture


def spec_at(tmp_path: Path) -> RunSpec:
    return parse_run_spec(
        _write_fixture(tmp_path), source_path=str(tmp_path / "run.yaml")
    )


def test_runtime_environment_change_must_block_undeclared_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = spec_at(tmp_path)
    monkeypatch.setenv("SEOCHO_MULTI_PLAN", "0")
    assert not multi_plan_enabled()
    before = collect_evidence(spec, only="query", force=False, track=False)
    monkeypatch.setenv("SEOCHO_MULTI_PLAN", "1")
    assert multi_plan_enabled()
    after = collect_evidence(spec, only="query", force=False, track=False)
    report = {
        "run": {"only": "query", "question_count": 1},
        "outcome": {"status": "completed"},
        "queries": [
            {"id": "1", "question": spec.questions[0].question, "answer": "Jane"}
        ],
    }
    compared = compare_runs(
        dict(report, reproducibility=before), dict(report, reproducibility=after)
    )
    assert not compared["comparable"], compared["observed_changes"]


def test_query_interruption_preserves_completed_first_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _write_fixture(tmp_path)
    raw["questions"] = ["first", "second"]
    spec = parse_run_spec(raw, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient({})
    completed = []

    def answer(question: str, **kwargs: Any) -> SimpleNamespace:
        if question == "second":
            raise KeyboardInterrupt()
        completed.append(question)
        return SimpleNamespace(response="completed answer")

    monkeypatch.setattr(client, "ask_response", answer)
    ctx = _fake_context(tmp_path, spec, client)
    with pytest.raises(KeyboardInterrupt):
        e2e.run(ctx, only="query", quiet=True)
    assert completed == ["first"]
    saved = json.loads((ctx.output_dir / "report.json").read_text())
    assert saved["outcome"]["status"] == "interrupted"
    assert len(saved["queries"]) == 1
    first = dict(saved["queries"][0])
    assert first.pop("latency_s") >= 0
    assert [first] == [
        {
            "id": "1",
            "question": "first",
            "answer": "completed answer",
            "empty": False,
        }
    ]


def test_saved_preflight_receipt_excludes_endpoint_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = spec_at(tmp_path)
    spec.graph = "bolt://demo:review-sentinel-password@localhost:7687?token=review-sentinel-token"
    from seocho.store import graph

    class UnavailableGraph:
        def __init__(self, *args: Any) -> None:
            raise RuntimeError("connection unavailable")

    monkeypatch.setattr(graph, "Neo4jGraphStore", UnavailableGraph)
    # This is the online preflight path used by a real failed run.
    from seocho.run_preflight import PreflightCheck

    checks = PreflightReport(
        [
            _check_graph(spec, online=True),
            PreflightCheck("documents", "fail", "invalid input"),
        ]
    )
    report = e2e._preflight_failure(spec, checks, directory=tmp_path / "failure")
    saved = report.report_json.read_text()
    assert "review-sentinel-password" not in saved
    assert "review-sentinel-token" not in saved


def test_degraded_extraction_must_not_be_cached_as_clean_indexing(
    tmp_path: Path,
) -> None:
    class Pipeline:
        def index(self, *args: Any, **kwargs: Any) -> IndexingResult:
            return IndexingResult(
                chunks_processed=1,
                total_nodes=2,
                fallback_used=True,
                fallback_reason="provider failed",
            )

    path = tmp_path / "doc.txt"
    path.write_text("Acme has a CEO")
    tracker = FileTracker(tmp_path)
    result = FileIndexer(Pipeline()).index_file(path, tracker=tracker)
    assert result.status == "failed"
    assert tracker.needs_indexing(path)


def test_receipt_v1_cannot_certify_runtime_settings(tmp_path: Path) -> None:
    spec = spec_at(tmp_path)
    receipt = collect_evidence(spec, only="query", force=False, track=False)
    receipt["schema_version"] = "seocho.run_evidence.v1"
    receipt["conditions"].pop("runtime_settings")
    report = {"run": {}, "reproducibility": receipt, "queries": []}
    result = compare_runs(report, report)
    assert not result["comparable"]
    assert all(m["delta"] is None for m in result["metrics"].values())


def test_declared_runtime_change_needs_hypothesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = spec_at(tmp_path)

    def receipt() -> dict[str, Any]:
        return {
            "run": {},
            "reproducibility": collect_evidence(
                spec, only="query", force=False, track=False
            ),
            "queries": [],
        }

    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "0")
    before = receipt()
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    after = receipt()
    with pytest.raises(ValueError, match="hypothesis"):
        compare_runs(before, after, changes=["runtime_settings"])
    assert compare_runs(
        before, after, changes=["runtime_settings"], hypothesis="Enable model routing"
    )["comparable"]


def test_agents_sdk_query_checkpoint_survives_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    from seocho import agents_runtime
    from seocho.integrations import openai_agents

    raw = _write_fixture(tmp_path)
    raw["questions"] = [{"id": "a", "question": "first", "expect": "answer"}, "second"]
    raw["agent"] = {"runtime": "agents_sdk", "ontology_bundle_dir": "bundle"}
    spec = parse_run_spec(raw, source_path=str(tmp_path / "run.yaml"))

    async def run_agent(**kwargs: Any) -> SimpleNamespace:
        if kwargs["input"] == "second":
            raise KeyboardInterrupt()
        return SimpleNamespace(final_output="answer")

    runtime = SimpleNamespace(trace=lambda *a: contextlib.nullcontext(), run=run_agent)
    monkeypatch.setattr(agents_runtime, "get_agents_runtime", lambda: runtime)
    monkeypatch.setattr(openai_agents, "build_graph_agent", lambda **kwargs: object())
    ctx = _fake_context(tmp_path, spec, _FakeClient({}))
    with pytest.raises(KeyboardInterrupt):
        e2e.run(ctx, only="query", quiet=True)
    saved = json.loads((ctx.output_dir / "report.json").read_text())
    assert saved["queries"][0]["answer"] == "answer"
    assert saved["queries"][0]["expect"] == "answer"
    assert saved["active_question"]["question"] == "second"
    assert saved["outcome"]["status"] == "interrupted"


def test_mixed_degraded_records_preserve_failure_reason(tmp_path: Path) -> None:
    class Pipeline:
        def index(self, content: str, **kwargs: Any) -> IndexingResult:
            return IndexingResult(
                chunks_processed=1,
                total_nodes=1,
                fallback_used=content == "bad",
                fallback_reason="provider failed" if content == "bad" else "",
            )

    file = tmp_path / "records.jsonl"
    file.write_text('{"content":"bad"}\n{"content":"good"}\n')
    result = FileIndexer(Pipeline()).index_file(file)
    assert result.status == "failed"
    assert result.indexing_result.fallback_used
    assert "provider failed" in result.error


def test_index_diagnostics_redact_nested_credentials(tmp_path: Path) -> None:
    spec = spec_at(tmp_path)
    spec.graph_password = "index-secret-sentinel"
    error = "failed bolt://user:index-secret-sentinel@localhost:7687?token=hidden"
    client = _FakeClient({})
    client.index_directory = lambda *a, **kw: {
        "files_failed": 1,
        "results": [
            {
                "status": "failed",
                "error": error,
                "indexing": {
                    "fallback_reason": error,
                    "write_errors": [error],
                    "validation_errors": [error],
                },
            }
        ],
    }
    ctx = _fake_context(tmp_path, spec, client)
    result = e2e.run(ctx, only="index", quiet=True)
    saved = result.report_json.read_text()
    assert "index-secret-sentinel" not in saved
    assert "token=hidden" not in saved
    assert "bolt://localhost:7687" in saved


def test_runtime_hint_content_changes_and_missing_files_are_evidence_gaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = spec_at(tmp_path)
    hints = tmp_path / "hints.json"
    hints.write_text('{"hint":"before"}')
    monkeypatch.setenv("ONTOLOGY_HINTS_PATH", str(hints))
    before = collect_evidence(spec, only="query", force=False, track=False)
    hints.write_text('{"hint":"after"}')
    after = collect_evidence(spec, only="query", force=False, track=False)
    assert (
        before["conditions"]["runtime_settings"]
        != after["conditions"]["runtime_settings"]
    )
    monkeypatch.setenv("ONTOLOGY_HINTS_PATH", str(tmp_path / "missing.json"))
    missing = collect_evidence(spec, only="query", force=False, track=False)
    assert "runtime_settings.ONTOLOGY_HINTS_PATH: FileNotFoundError" in missing["gaps"]
    report = {"run": {}, "queries": [], "reproducibility": missing}
    assert not compare_runs(report, report)["comparable"]
