from __future__ import annotations

import json
import textwrap
from typing import Any, Dict, List

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho import e2e
from seocho.models import AskResponse
from seocho.run_spec import parse_run_spec


class _DummyGraphStore:
    def ensure_constraints(self, ontology: Any) -> None:
        pass

    def close(self) -> None:
        pass


class _DummyLLM:
    def __init__(self, ref: str) -> None:
        self.ref = ref


def _ontology() -> Ontology:
    return Ontology(
        name="run_demo",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={"CEO_OF": RelDef(source="Person", target="Company")},
    )


def _write_fixture(tmp_path) -> Dict[str, Any]:
    _ontology().to_yaml(tmp_path / "schema.yaml")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "acme.md").write_text("Jane Park is the CEO of Acme.", encoding="utf-8")
    return {
        "ontology": "schema.yaml",
        "documents": "docs",
        "questions": ["Who is the CEO of Acme?"],
    }


def _patch_backends(monkeypatch) -> Dict[str, Any]:
    created: Dict[str, Any] = {"llms": [], "stores": []}

    def fake_llm(ref: str) -> _DummyLLM:
        llm = _DummyLLM(ref)
        created["llms"].append(llm)
        return llm

    def fake_store(spec: Any, ontology: Any) -> _DummyGraphStore:
        store = _DummyGraphStore()
        created["stores"].append(store)
        return store

    monkeypatch.setattr(e2e, "_build_llm", fake_llm)
    monkeypatch.setattr(e2e, "_build_graph_store", fake_store)
    return created


def test_build_single_client_when_models_match(tmp_path, monkeypatch) -> None:
    created = _patch_backends(monkeypatch)
    payload = _write_fixture(tmp_path)
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))

    ctx = e2e.build(spec)
    try:
        assert ctx.index_client is ctx.query_client
        assert len(created["llms"]) == 1
        assert len(created["stores"]) == 1
        assert ctx.index_client.workspace_id == "run"
    finally:
        ctx.close()


def test_build_two_clients_share_store_and_workspace(tmp_path, monkeypatch) -> None:
    created = _patch_backends(monkeypatch)
    payload = _write_fixture(tmp_path)
    payload["models"] = {"indexing": "mara/MiniMax-M2", "query": "mara/MiniMax-M2.5"}
    payload["workspace_id"] = "shared_ws"
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))

    ctx = e2e.build(spec)
    try:
        assert ctx.index_client is not ctx.query_client
        assert len(created["llms"]) == 2
        assert {llm.ref for llm in created["llms"]} == {"mara/MiniMax-M2", "mara/MiniMax-M2.5"}
        assert len(created["stores"]) == 1
        assert ctx.index_client.graph_store is ctx.query_client.graph_store
        assert ctx.index_client.workspace_id == "shared_ws"
        assert ctx.query_client.workspace_id == "shared_ws"
    finally:
        ctx.close()


def test_build_agent_config_inline_overrides() -> None:
    spec = parse_run_spec(
        {
            "ontology": "schema.yaml",
            "documents": "docs",
            "agent": {"execution_mode": "supervisor", "routing_policy": "thorough"},
            "query": {"reasoning_mode": False, "repair_budget": 7, "answer_style": "table"},
        }
    )
    config = e2e.build_agent_config(spec)
    assert config.execution_mode == "supervisor"
    assert config.handoff is True
    assert config.reasoning_mode is False
    assert config.repair_budget == 7
    assert config.answer_style == "table"
    assert config.routing_policy is not None


def test_build_agent_config_strict_defaults_validation_to_reject() -> None:
    base = {"ontology": {"path": "s.yaml", "enforcement": "strict"}, "documents": "docs"}
    config = e2e.build_agent_config(parse_run_spec(base))
    assert config.validation_on_fail == "reject"

    guided = {"ontology": {"path": "s.yaml", "enforcement": "guided"}, "documents": "docs"}
    assert e2e.build_agent_config(parse_run_spec(guided)).validation_on_fail == "warn"


def test_build_agent_config_from_design_with_inline_override(tmp_path) -> None:
    design = tmp_path / "agent_design.yaml"
    design.write_text(
        textwrap.dedent(
            """
            name: demo-pattern
            pattern: memory_tool_use
            ontology:
              profile: demo
            """
        ).strip(),
        encoding="utf-8",
    )
    spec = parse_run_spec(
        {
            "ontology": "schema.yaml",
            "documents": "docs",
            "agent": {"design": "agent_design.yaml", "execution_mode": "pipeline"},
        },
        source_path=str(tmp_path / "run.yaml"),
    )
    config = e2e.build_agent_config(spec)
    # pattern default (memory_tool_use -> agent) overridden by inline key
    assert config.execution_mode == "pipeline"
    assert config.extra.get("agent_design_pattern") == "memory_tool_use"


class _FakeClient:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self.answers = answers
        self.index_calls: List[Dict[str, Any]] = []
        self.ask_calls: List[Dict[str, Any]] = []
        self.workspace_id = "fake"

    def index_directory(self, directory: str, **kwargs: Any) -> Dict[str, Any]:
        self.index_calls.append({"directory": directory, **kwargs})
        return {
            "directory": directory,
            "files_found": 2,
            "files_indexed": 2,
            "files_skipped": 0,
            "files_failed": 0,
            "files_unchanged": 0,
            "results": [
                {"path": "a.md", "status": "indexed",
                 "indexing": {"total_nodes": 3, "total_relationships": 2, "validation_errors": []}},
                {"path": "b.md", "status": "indexed",
                 "indexing": {"total_nodes": 1, "total_relationships": 0, "validation_errors": ["warn"]}},
            ],
        }

    def ask(self, question: str, **kwargs: Any) -> str:
        self.ask_calls.append({"question": question, **kwargs})
        answer = self.answers[question]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def ask_response(self, question: str, **kwargs: Any) -> AskResponse:
        self.ask_calls.append({"question": question, **kwargs})
        answer = self.answers[question]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, AskResponse):
            return answer
        envelope: Dict[str, Any] = {
            "schema_version": "answer_envelope.v1",
            "answer": str(answer),
            "support_assessment": {},
            "evidence_bundle": {},
            "strategy_decision": {},
        }
        if answer:
            envelope["support_assessment"] = {
                "intent_id": "company_ceo",
                "status": "supported",
                "supported": True,
                "coverage": 1.0,
                "missing_slots": [],
            }
            envelope["evidence_bundle"] = {
                "intent_id": "company_ceo",
                "coverage": 1.0,
                "missing_slots": [],
                "selected_triples": [
                    {"source": "Jane Park", "relation": "CEO_OF", "target": "Acme Corp"}
                ],
            }
            envelope["strategy_decision"] = {"support_status": "supported"}
        return AskResponse(
            response=str(answer),
            runtime_mode="semantic",
            answer_envelope=envelope,
        )

    def close(self) -> None:
        pass


def _fake_context(tmp_path, spec, client) -> e2e.RunContext:
    return e2e.RunContext(
        spec=spec,
        ontology=_ontology(),
        graph_store=_DummyGraphStore(),
        index_client=client,
        query_client=client,
        database="rundemolpg",
        documents_path=tmp_path / "docs",
        output_dir=tmp_path / "out",
    )


def test_run_produces_report_and_continues_on_question_error(tmp_path) -> None:
    payload = _write_fixture(tmp_path)
    payload["questions"] = ["good?", "boom?", "empty?"]
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient(
        {"good?": "Jane Park", "boom?": RuntimeError("backend down"), "empty?": ""}
    )
    ctx = _fake_context(tmp_path, spec, client)

    report = e2e.run(ctx, quiet=True)

    assert report.report_json is not None and report.report_json.exists()
    assert report.report_md is not None and report.report_md.exists()
    with open(report.report_json, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["indexing"]["total_nodes"] == 4
    assert payload["indexing"]["total_relationships"] == 2
    assert payload["indexing"]["validation_errors_count"] == 1
    queries = payload["queries"]
    assert len(queries) == 3
    assert queries[0]["answer"] == "Jane Park"
    assert queries[0]["runtime_mode"] == "semantic"
    assert queries[0]["support_status"] == "supported"
    assert queries[0]["selected_triple_count"] == 1
    assert queries[0]["evidence_bundle"]["selected_triples"][0]["relation"] == "CEO_OF"
    assert "backend down" in queries[1]["error"]
    assert queries[2]["empty"] is True
    report_md = report.report_md.read_text(encoding="utf-8")
    assert "| # | question | answered | support | missing | evidence | latency |" in report_md
    assert "`Jane Park` -[CEO_OF]-> `Acme Corp`" in report_md
    # a question error means the run is reported as failed
    assert report.ok is False
    # strict_validation passthrough reaches the indexing call
    assert client.index_calls[0]["strict_validation"] is False


def test_run_strict_enforcement_passes_strict_validation(tmp_path) -> None:
    payload = _write_fixture(tmp_path)
    payload["ontology"] = {"path": "schema.yaml", "enforcement": "strict"}
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient({"Who is the CEO of Acme?": "Jane Park"})
    ctx = _fake_context(tmp_path, spec, client)

    report = e2e.run(ctx, quiet=True)
    assert client.index_calls[0]["strict_validation"] is True
    assert report.ok is True


def test_run_index_only_when_no_questions(tmp_path) -> None:
    payload = _write_fixture(tmp_path)
    payload.pop("questions")
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient({})
    ctx = _fake_context(tmp_path, spec, client)

    report = e2e.run(ctx, quiet=True)
    assert "queries" not in report.payload
    assert client.ask_calls == []
    assert report.ok is True
    assert "Index-only run" in report.report_md.read_text(encoding="utf-8")


def test_run_only_index_skips_query_phase(tmp_path) -> None:
    payload = _write_fixture(tmp_path)
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient({"Who is the CEO of Acme?": "Jane Park"})
    ctx = _fake_context(tmp_path, spec, client)

    report = e2e.run(ctx, only="index", quiet=True)
    assert client.ask_calls == []
    assert "indexing" in report.payload
    assert "queries" not in report.payload


def test_run_query_params_forwarded(tmp_path) -> None:
    payload = _write_fixture(tmp_path)
    payload["query"] = {"reasoning_mode": False, "repair_budget": 3, "limit": 9}
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))
    client = _FakeClient({"Who is the CEO of Acme?": "Jane Park"})
    ctx = _fake_context(tmp_path, spec, client)

    e2e.run(ctx, only="query", quiet=True)
    call = client.ask_calls[0]
    assert call["reasoning_mode"] is False
    assert call["repair_budget"] == 3
    assert call["limit"] == 9
    assert call["database"] == "rundemolpg"


def test_file_indexer_strict_validation_set_and_restored(tmp_path) -> None:
    """The passthrough must set/restore the pipeline flag (facade pattern)."""
    from seocho.index.file_reader import FileIndexer

    class _RecordingPipeline:
        def __init__(self) -> None:
            self.strict_validation = False
            self.seen: List[bool] = []

        def index(self, content: str, **kwargs: Any) -> Any:
            from seocho.index.pipeline import IndexingResult

            self.seen.append(self.strict_validation)
            return IndexingResult(source_id="s1", chunks_processed=1, total_nodes=1)

    pipeline = _RecordingPipeline()
    indexer = FileIndexer(pipeline)
    doc = tmp_path / "doc.md"
    doc.write_text("Acme content", encoding="utf-8")

    result = indexer.index_file(doc, strict_validation=True)
    assert result.status == "indexed"
    assert pipeline.seen == [True]
    assert pipeline.strict_validation is False  # restored

    indexer.index_file(doc)
    assert pipeline.seen == [True, False]  # default untouched


def test_build_graph_store_respects_explicit_kind(monkeypatch) -> None:
    """graph.kind dispatches the backend even without URI sniffing."""
    import seocho.store.graph as graph_mod

    captured: Dict[str, Any] = {}

    class _FakeNeo4j:
        def __init__(self, uri: str, user: str, password: str) -> None:
            captured["neo4j"] = (uri, user, password)

    class _FakeLadybug:
        def __init__(self, path: str) -> None:
            captured["ladybug"] = path

        def ensure_constraints(self, ontology: Any) -> None:
            pass

    monkeypatch.setattr(graph_mod, "Neo4jGraphStore", _FakeNeo4j)
    monkeypatch.setattr(graph_mod, "LadybugGraphStore", _FakeLadybug)

    bolt_spec = parse_run_spec(
        {"ontology": "s.yaml", "documents": "d/",
         "graph": {"kind": "dozerdb", "uri": "bolt://h:7687", "user": "u", "password": "p"}}
    )
    e2e._build_graph_store(bolt_spec, _ontology())
    assert captured["neo4j"] == ("bolt://h:7687", "u", "p")

    embedded_spec = parse_run_spec({"ontology": "s.yaml", "documents": "d/"})
    e2e._build_graph_store(embedded_spec, _ontology())
    assert captured["ladybug"] == ".seocho/local.lbug"


def test_build_vector_store_absent_section_returns_none() -> None:
    spec = parse_run_spec({"ontology": "s.yaml", "documents": "d/"})
    assert e2e._build_vector_store(spec) is None


def test_build_vector_store_fastembed_default(monkeypatch) -> None:
    import seocho.store.fastembed_backend as fe_mod
    import seocho.store.vector as vec_mod

    class _FakeEmbedding:
        def embed(self, texts):
            return [[0.0] * 384 for _ in texts]

    captured: Dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "VECTOR_STORE"

    monkeypatch.setattr(fe_mod, "make_fastembed_backend", lambda *a, **k: _FakeEmbedding())
    monkeypatch.setattr(vec_mod, "create_vector_store", fake_create)

    spec = parse_run_spec(
        {"ontology": "s.yaml", "documents": "d/", "vector": {"kind": "faiss"}}
    )
    assert e2e._build_vector_store(spec) == "VECTOR_STORE"
    assert captured["kind"] == "faiss"
    assert captured["dimension"] == 384  # derived from the probe embedding
    assert isinstance(captured["embedding_backend"], _FakeEmbedding)


def test_build_vector_store_fastembed_missing_is_loud(monkeypatch) -> None:
    import seocho.store.fastembed_backend as fe_mod

    monkeypatch.setattr(fe_mod, "make_fastembed_backend", lambda *a, **k: None)
    spec = parse_run_spec(
        {"ontology": "s.yaml", "documents": "d/", "vector": {"kind": "faiss"}}
    )
    with pytest.raises(RuntimeError, match="fastembed is unavailable"):
        e2e._build_vector_store(spec)


def test_build_passes_vector_store_to_clients(tmp_path, monkeypatch) -> None:
    created = _patch_backends(monkeypatch)
    monkeypatch.setattr(e2e, "_build_vector_store", lambda spec: "SHARED_VECTORS")
    payload = _write_fixture(tmp_path)
    payload["vector"] = {"kind": "faiss"}
    spec = parse_run_spec(payload, source_path=str(tmp_path / "run.yaml"))

    ctx = e2e.build(spec)
    try:
        assert ctx.index_client.vector_store == "SHARED_VECTORS"
        assert ctx.query_client.vector_store == "SHARED_VECTORS"
        assert created["stores"], "graph store still built"
    finally:
        ctx.close()
