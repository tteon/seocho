from __future__ import annotations

import json
from pathlib import Path

import pytest

from seocho import e2e
from seocho.run_comparison import compare_runs, load_report
from seocho.run_evidence import collect_evidence
from seocho.run_outcomes import summarize_outcome
from seocho.run_reporting import FileReportStore
from seocho.run_spec import RunSpec, RunSpecError, parse_run_spec
from test_e2e_runner import _FakeClient, _fake_context, _patch_backends, _write_fixture


def _spec(tmp_path: Path) -> RunSpec:
    return parse_run_spec(
        _write_fixture(tmp_path), source_path=str(tmp_path / "run.yaml")
    )


@pytest.mark.parametrize(
    "summary,expected",
    [
        ({"files_found": 2, "files_indexed": 1, "files_failed": 1}, "partial"),
        ({"files_found": 1, "files_skipped": 1}, "failed"),
        ({"files_found": 0}, "failed"),
        ({"files_found": 2, "files_unchanged": 2}, "completed"),
    ],
)
def test_indexing_outcomes(summary: dict, expected: str) -> None:
    assert summarize_outcome({"indexing": summary})["status"] == expected


def test_empty_answer_is_not_success() -> None:
    assert (
        summarize_outcome({"queries": [{"id": "q", "answer": "  "}]})["status"]
        == "failed"
    )


def test_index_crash_preserves_report_and_skips_query(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    client = _FakeClient({})

    def fail(*args, **kwargs):
        raise RuntimeError("graph write unavailable")

    monkeypatch.setattr(client, "index_directory", fail)
    ctx = _fake_context(tmp_path, spec, client)
    report = e2e.run(ctx, quiet=True)
    saved = json.loads(report.report_json.read_text())
    assert not report.ok
    assert saved["fatal_error"]["stage"] == "index"
    assert "graph write unavailable" in report.report_md.read_text()
    assert client.ask_calls == []


def test_no_usable_documents_does_not_query_old_graph(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    client = _FakeClient({})
    monkeypatch.setattr(
        client,
        "index_directory",
        lambda *a, **k: {
            "files_found": 1,
            "files_failed": 1,
            "results": [{"path": "bad.pdf", "status": "failed", "error": "read error"}],
        },
    )
    report = e2e.run(_fake_context(tmp_path, spec, client), quiet=True)
    assert not report.ok
    assert report.payload["queries"][0]["skipped"]
    assert client.ask_calls == []
    assert "read error" in report.report_md.read_text()


def test_build_failure_has_report_and_closes_partial_resources(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    made = _patch_backends(monkeypatch)
    closed = []
    from seocho import client

    def fail(**kwargs):
        kwargs["graph_store"].close = lambda: closed.append("graph")
        raise RuntimeError("cannot initialize client")

    monkeypatch.setattr(client, "Seocho", fail)
    report = e2e.run_spec_once(spec, quiet=True)
    assert report.payload["fatal_error"]["stage"] == "build"
    assert report.report_json.is_file()
    assert made["stores"] and closed == ["graph"]


def test_interruption_preserves_last_phase_and_releases_client(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    client = _FakeClient({})
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True))

    def stop(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(client, "index_directory", stop)
    monkeypatch.setattr(
        e2e, "build", lambda spec: _fake_context(tmp_path, spec, client)
    )
    out = tmp_path / "interrupted"
    with pytest.raises(KeyboardInterrupt):
        e2e.run_spec_once(spec, quiet=True, output_dir_override=out)
    saved = json.loads((out / "report.json").read_text())
    assert saved["outcome"]["status"] == "interrupted"
    assert saved["active_stage"] == "index"
    assert closed == [True]


def test_existing_report_rejected_before_backend_build(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    out = tmp_path / "old"
    out.mkdir()
    (out / "report.json").write_text("original")
    monkeypatch.setattr(e2e, "build", lambda spec: pytest.fail("must not build"))
    with pytest.raises(FileExistsError):
        e2e.run_spec_once(spec, output_dir_override=out)
    assert (out / "report.json").read_text() == "original"


def test_partial_failure_survives_file_report_roundtrip(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path / "run")
    payload = {
        "run": {},
        "indexing": {"files_found": 2, "files_indexed": 1, "files_failed": 1},
    }
    report = store.write(payload)
    assert not report.ok
    assert load_report(report.report_json)["indexing"]["files_failed"] == 1


def _report(spec: RunSpec, answer: str = "Jane Park") -> dict:
    return {
        "run": {"name": "test"},
        "outcome": {"status": "completed"},
        "reproducibility": collect_evidence(spec, only=None, force=False, track=True),
        "indexing": {"files_found": 1, "files_indexed": 1},
        "queries": [
            {
                "id": "1",
                "question": spec.questions[0].question,
                "answer": answer,
                "expect": "Jane Park",
                "latency_s": 2.0,
            }
        ],
    }


def test_comparison_matches_questions_and_shows_empty_to_answered(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    result = compare_runs(_report(spec, ""), _report(spec))
    assert result["comparable"]
    assert result["questions"][0]["transition"] == "empty -> answered"
    assert result["metrics"]["agent.answered_rate"]["delta"] == 1.0
    assert result["metrics"]["usage.cost_usd"]["delta"] is None


def test_changed_document_content_blocks_comparison(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    before = _report(spec)
    (tmp_path / "docs" / "acme.md").write_text("Different dataset")
    result = compare_runs(before, _report(spec))
    assert not result["comparable"]
    assert "documents" in result["observed_changes"]
    assert all(m["delta"] is None for m in result["metrics"].values())


def test_model_change_requires_declared_hypothesis(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    before = _report(spec)
    spec.models["default"] = "mara/another-model"
    after = _report(spec)
    assert not compare_runs(before, after)["comparable"]
    with pytest.raises(ValueError, match="hypothesis"):
        compare_runs(before, after, changes=["models"])
    result = compare_runs(
        before,
        after,
        changes=["models"],
        hypothesis="Model replacement reduces empty responses",
    )
    assert result["comparable"]
    assert result["verdict"] == "descriptive_comparison"


def test_missing_questions_cannot_be_hidden_by_matched_subset(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    before, after = _report(spec), _report(spec)
    after["queries"] = []
    result = compare_runs(before, after)
    assert not result["comparable"]
    assert result["questions"][0]["candidate_state"] == "missing"


def test_legacy_report_is_diagnostic_only(tmp_path: Path) -> None:
    before = _report(_spec(tmp_path))
    before.pop("reproducibility")
    assert not compare_runs(before, before)["comparable"]


def test_duplicate_resolved_ids_are_rejected() -> None:
    with pytest.raises(RunSpecError, match="duplicate"):
        parse_run_spec(
            {
                "ontology": "s.yaml",
                "documents": "docs",
                "graph": "bolt://localhost",
                "questions": [{"id": "2", "question": "first"}, "second"],
            }
        )


def test_secrets_not_recorded_in_evidence(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec.graph = "bolt://user:private-password@localhost:7687?token=private-token"
    encoded = json.dumps(collect_evidence(spec, only=None, force=False, track=True))
    assert "private-password" not in encoded and "private-token" not in encoded


def test_preflight_finds_bad_jsonl_before_any_backend_call(tmp_path: Path) -> None:
    from seocho.run_preflight import _check_document_content

    spec = _spec(tmp_path)
    (tmp_path / "docs" / "bad.jsonl").write_text('{"text": "valid"}\nnot-json\n')
    check = _check_document_content(spec)
    assert not check.ok and "line 2" in check.detail
    assert "--dry-run" in check.fix


def test_preflight_checks_actual_database_and_closes_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from seocho.run_preflight import _check_graph
    from seocho.store import graph

    spec = _spec(tmp_path)
    spec.database = "experimentdb"
    calls = []

    class Store:
        def __init__(self, *args):
            pass

        def query(self, cypher, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("target database unavailable")

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(graph, "Neo4jGraphStore", Store)
    check = _check_graph(spec, online=True)
    assert not check.ok
    assert calls == [
        {"database": "experimentdb", "workspace_id": spec.resolved_workspace_id()},
        "closed",
    ]
    assert "embedded" not in check.fix


def test_unknown_evidence_metrics_stay_unavailable(tmp_path: Path) -> None:
    report = _report(_spec(tmp_path))
    result = compare_runs(report, report)
    assert result["metrics"]["agent.missing_slots_per_question"]["delta"] is None
    assert result["metrics"]["agent.evidence_available_rate"]["baseline"] is None


def test_compare_cli_writes_new_artifacts_and_refuses_overwrite(
    tmp_path: Path, capsys
) -> None:
    from seocho.cli import main

    report = _report(_spec(tmp_path))
    for arm in ("before", "after"):
        path = tmp_path / arm
        path.mkdir()
        (path / "report.json").write_text(json.dumps(report))
    args = [
        "runs",
        "compare",
        str(tmp_path / "before"),
        str(tmp_path / "after"),
        "--output-dir",
        str(tmp_path / "comparison"),
        "--json",
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["comparable"]
    saved = (tmp_path / "comparison" / "comparison.json").read_bytes()
    assert main(args) == 1
    assert (tmp_path / "comparison" / "comparison.json").read_bytes() == saved


def test_preflight_failure_is_saved_for_real_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from seocho.run_preflight import PreflightCheck, PreflightReport

    spec = _spec(tmp_path)
    monkeypatch.setattr(e2e, "load_run_spec", lambda path: spec)
    monkeypatch.setattr(
        e2e,
        "run_preflight",
        lambda *a, **k: PreflightReport(
            [PreflightCheck("documents", "fail", "bad input", "Fix the input")]
        ),
    )
    monkeypatch.setattr(e2e, "build", lambda spec: pytest.fail("must not build"))
    assert e2e.run_from_config("run.yaml", json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["active_stage"] == "preflight"
    saved = Path(payload["report_directory"]) / "report.json"
    assert saved.is_file()
    assert payload["preflight"][0]["fix"] == "Fix the input"


def test_explicit_source_change_requires_same_inputs(tmp_path: Path) -> None:
    before = _report(_spec(tmp_path))
    after = json.loads(json.dumps(before))
    after["reproducibility"]["conditions"]["source"] = "f" * 64
    assert not compare_runs(before, after)["comparable"]
    result = compare_runs(
        before, after, changes=["source"], hypothesis="Index error handling changed"
    )
    assert result["comparable"]
    after["reproducibility"]["conditions"]["documents"] = "f" * 64
    assert not compare_runs(
        before, after, changes=["source"], hypothesis="Index error handling changed"
    )["comparable"]


def test_cleanup_failure_is_reported_without_losing_answers(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(tmp_path)
    client = _FakeClient({spec.questions[0].question: "Jane Park"})

    def fail():
        raise RuntimeError("close failed")

    monkeypatch.setattr(client, "close", fail)
    monkeypatch.setattr(
        e2e, "build", lambda spec: _fake_context(tmp_path, spec, client)
    )
    report = e2e.run_spec_once(spec, quiet=True)
    assert not report.ok
    assert report.payload["queries"][0]["answer"] == "Jane Park"
    assert (
        json.loads(report.report_json.read_text())["cleanup_errors"][0]["stage"]
        == "cleanup"
    )
