from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import pytest

from seocho import e2e


class _DummyGraphStore:
    def ensure_constraints(self, ontology: Any) -> None:
        pass

    def close(self) -> None:
        pass


class _DummyLLM:
    def __init__(self, ref: str) -> None:
        self.ref = ref


class _FakeClient:
    """Stands in for both index and query clients per variant."""

    instances: List["_FakeClient"] = []

    def __init__(self, *, fail_query: bool = False) -> None:
        self.fail_query = fail_query
        self.index_calls: List[Dict[str, Any]] = []
        self.ask_calls: List[Dict[str, Any]] = []
        self.workspace_id = "fake"
        _FakeClient.instances.append(self)

    def index_directory(self, directory: str, **kwargs: Any) -> Dict[str, Any]:
        self.index_calls.append({"directory": directory, **kwargs})
        return {
            "directory": directory,
            "files_found": 1,
            "files_indexed": 1,
            "files_skipped": 0,
            "files_failed": 0,
            "files_unchanged": 0,
            "results": [
                {"path": "a.md", "status": "indexed",
                 "indexing": {"total_nodes": 2, "total_relationships": 1,
                              "validation_errors": []}},
            ],
        }

    def ask(self, question: str, **kwargs: Any) -> str:
        self.ask_calls.append({"question": question, **kwargs})
        if self.fail_query:
            raise RuntimeError("query backend down")
        return "answer"

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeClient.instances = []
    yield
    _FakeClient.instances = []


def _write_sweep(tmp_path, *, variants=("alpha", "beta"), template_extra="") -> Path:
    (tmp_path / "schema.yaml").write_text(
        textwrap.dedent(
            """
            name: sweepdemo
            nodes:
              Company:
                properties:
                  name:
                    type: STRING
                    constraint: UNIQUE
            relationships: {}
            """
        ).strip(),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "a.md").write_text("Acme is a company.", encoding="utf-8")

    template = tmp_path / "run.yaml.j2"
    template.write_text(
        textwrap.dedent(
            f"""
            name: demo
            ontology: ./schema.yaml
            documents: ./docs/
            models:
              default: "{{{{ model | default('mara/MiniMax-M2.5') }}}}"
            questions:
              - Who is the CEO?
            {template_extra}
            """
        ).strip(),
        encoding="utf-8",
    )
    sweep = tmp_path / "seocho.sweep.yaml"
    variant_lines = "\n".join(f"  - name: {name}" for name in variants)
    sweep.write_text(
        f"name: demo-sweep\ntemplate: ./run.yaml.j2\nvariants:\n{variant_lines}\n",
        encoding="utf-8",
    )
    return sweep


def _patch_backends(monkeypatch, *, fail_variant: str = "") -> Dict[str, Any]:
    created: Dict[str, Any] = {"graphs": [], "specs": []}

    def fake_llm(ref: str) -> _DummyLLM:
        return _DummyLLM(ref)

    def fake_store(spec: Any, ontology: Any) -> _DummyGraphStore:
        created["graphs"].append(spec.graph)
        created["specs"].append(spec)
        return _DummyGraphStore()

    real_build = e2e.build

    def tracked_build(spec):
        ctx = real_build(spec)
        fail = fail_variant and fail_variant in spec.name
        client = _FakeClient(fail_query=bool(fail))
        ctx.index_client = client
        ctx.query_client = client
        return ctx

    monkeypatch.setattr(e2e, "_build_llm", fake_llm)
    monkeypatch.setattr(e2e, "_build_graph_store", fake_store)
    monkeypatch.setattr(e2e, "build", tracked_build)
    # preflight: skip the real API-key/graph checks for unit tests
    monkeypatch.setattr(
        e2e, "run_preflight",
        lambda spec, online=False: type(
            "R", (), {"ok": True, "render": lambda self: "  ok    (stubbed)",
                      "failures": lambda self: []}
        )(),
    )
    return created


def test_sweep_variants_are_isolated(tmp_path, monkeypatch) -> None:
    created = _patch_backends(monkeypatch)
    sweep_path = _write_sweep(tmp_path)

    code = e2e.run_sweep_from_config(sweep_path, json_output=True)
    assert code == 0

    # distinct graph paths under the sweep run dir, one per variant
    assert len(created["graphs"]) == 2
    assert created["graphs"][0] != created["graphs"][1]
    assert all("graph.lbug" in graph for graph in created["graphs"])
    # distinct workspaces (response-cache key invariant)
    workspaces = [spec.workspace_id for spec in created["specs"]]
    assert workspaces[0] != workspaces[1]
    # tracking disabled for every variant index call
    for client in _FakeClient.instances:
        for call in client.index_calls:
            assert call["track"] is False


def test_sweep_writes_summary_and_artifacts(tmp_path, monkeypatch) -> None:
    _patch_backends(monkeypatch)
    sweep_path = _write_sweep(tmp_path)

    code = e2e.run_sweep_from_config(sweep_path, json_output=True)
    assert code == 0

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    sweep_dir = run_dirs[0]
    with open(sweep_dir / "summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)
    assert [row["variant"] for row in summary["variants"]] == ["alpha", "beta"]
    assert all(row["status"] == "ok" for row in summary["variants"])
    md = (sweep_dir / "summary.md").read_text(encoding="utf-8")
    assert "| alpha | ok |" in md
    for variant in ("alpha", "beta"):
        assert (sweep_dir / variant / "rendered.yaml").exists()
        assert (sweep_dir / variant / "report.json").exists()
        rendered = (sweep_dir / variant / "rendered.yaml").read_text(encoding="utf-8")
        assert str(tmp_path) in rendered  # paths absolutized for reproduction


def test_sweep_keeps_going_after_variant_failure(tmp_path, monkeypatch) -> None:
    _patch_backends(monkeypatch, fail_variant="alpha")
    sweep_path = _write_sweep(tmp_path)

    code = e2e.run_sweep_from_config(sweep_path, json_output=True)
    assert code == 1  # one variant failed

    sweep_dir = next((tmp_path / "runs").iterdir())
    with open(sweep_dir / "summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)
    by_name = {row["variant"]: row for row in summary["variants"]}
    assert by_name["alpha"]["status"] == "failed"  # query error -> RunReport.ok False
    assert by_name["beta"]["status"] == "ok"  # still executed
    assert summary["sweep"]["failed_variants"] == ["alpha"]


def test_sweep_fail_fast_stops_after_first_failure(tmp_path, monkeypatch) -> None:
    _patch_backends(monkeypatch, fail_variant="alpha")
    sweep_path = _write_sweep(tmp_path)

    code = e2e.run_sweep_from_config(sweep_path, json_output=True, fail_fast=True)
    assert code == 1
    sweep_dir = next((tmp_path / "runs").iterdir())
    with open(sweep_dir / "summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)
    assert [row["variant"] for row in summary["variants"]] == ["alpha"]


def test_sweep_only_variant_subset(tmp_path, monkeypatch) -> None:
    _patch_backends(monkeypatch)
    sweep_path = _write_sweep(tmp_path, variants=("alpha", "beta", "gamma"))

    code = e2e.run_sweep_from_config(
        sweep_path, json_output=True, only_variants=["beta"]
    )
    assert code == 0
    sweep_dir = next((tmp_path / "runs").iterdir())
    with open(sweep_dir / "summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)
    assert [row["variant"] for row in summary["variants"]] == ["beta"]

    assert e2e.run_sweep_from_config(
        sweep_path, json_output=True, only_variants=["nope"]
    ) == 2


def test_sweep_stage1_config_error_runs_nothing(tmp_path, monkeypatch, capsys) -> None:
    created = _patch_backends(monkeypatch)
    sweep_path = _write_sweep(tmp_path)
    # break the template for every variant
    (tmp_path / "run.yaml.j2").write_text(
        'ontology: ./schema.yaml\ndocuments: ./docs/\nmodels:\n  default: "{{ missing_model }}"\n',
        encoding="utf-8",
    )

    code = e2e.run_sweep_from_config(sweep_path, json_output=True)
    assert code == 2
    assert created["graphs"] == []  # nothing was built
    err = capsys.readouterr().err
    assert "variant alpha" in err and "variant beta" in err
    assert "missing_model" in err


def test_sweep_var_flag_applies_to_all_variants(tmp_path, monkeypatch) -> None:
    created = _patch_backends(monkeypatch)
    sweep_path = _write_sweep(tmp_path)

    code = e2e.run_sweep_from_config(
        sweep_path, json_output=True, var_flags=["model=kimi/kimi-k2.5"]
    )
    assert code == 0
    assert all(
        spec.indexing_model() == "kimi/kimi-k2.5" for spec in created["specs"]
    )


def test_run_from_config_rejects_vars_for_plain_yaml(tmp_path, capsys) -> None:
    config = tmp_path / "run.yaml"
    config.write_text("ontology: s.yaml\ndocuments: docs\n", encoding="utf-8")
    code = e2e.run_from_config(config, var_flags=["a=1"])
    assert code == 2
    assert ".j2" in capsys.readouterr().err


def test_run_from_config_show_rendered(tmp_path, capsys) -> None:
    (tmp_path / "schema.yaml").write_text("name: t\nnodes: {}\nrelationships: {}\n")
    template = tmp_path / "run.yaml.j2"
    template.write_text(
        'ontology: ./schema.yaml\ndocuments: ./docs/\nmodels:\n  default: "{{ model }}"\n',
        encoding="utf-8",
    )
    code = e2e.run_from_config(
        template, var_flags=["model=mara/MiniMax-M2"], show_rendered=True
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "mara/MiniMax-M2" in out


def test_file_indexer_track_false_leaves_no_state(tmp_path) -> None:
    from seocho.index.file_reader import FileIndexer
    from seocho.index.pipeline import IndexingResult

    class _RecordingPipeline:
        strict_validation = False

        def index(self, content: str, **kwargs: Any) -> IndexingResult:
            return IndexingResult(source_id="s", chunks_processed=1, total_nodes=1)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("content", encoding="utf-8")

    indexer = FileIndexer(_RecordingPipeline())
    first = indexer.index_directory(docs, track=False)
    assert first.files_indexed == 1
    assert not (docs / ".seocho_index").exists()

    second = indexer.index_directory(docs, track=False)
    assert second.files_indexed == 1  # no "unchanged" skips
    assert second.files_unchanged == 0

    tracked = indexer.index_directory(docs)  # default behavior unchanged
    assert (docs / ".seocho_index").exists()
    again = indexer.index_directory(docs)
    assert again.files_unchanged == 1
