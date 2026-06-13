from __future__ import annotations

import textwrap

import pytest

from seocho.run_spec import (
    DEFAULT_MODEL,
    RUN_SPEC_TEMPLATE,
    RunSpecError,
    load_run_spec,
    parse_model_ref,
    parse_run_spec,
)


def _minimal_payload() -> dict:
    return {
        "ontology": "./schema.yaml",
        "documents": "./docs/",
        "questions": ["Who is the CEO of Acme?"],
    }


def test_minimal_spec_defaults() -> None:
    spec = parse_run_spec(_minimal_payload(), source_path="demo.yaml")
    assert spec.name == "demo"
    assert spec.ontology_path == "./schema.yaml"
    assert spec.documents_path == "./docs/"
    assert spec.enforcement == "guided"
    assert spec.strict_validation() is False
    assert spec.indexing_model() == DEFAULT_MODEL
    assert spec.query_model() == DEFAULT_MODEL
    assert spec.uses_split_models() is False
    assert spec.output_dir == "runs"
    assert len(spec.questions) == 1


def test_template_round_trip() -> None:
    import yaml

    spec = parse_run_spec(yaml.safe_load(RUN_SPEC_TEMPLATE), source_path="seocho.run.yaml")
    assert spec.ontology_path == "./schema.yaml"
    assert len(spec.questions) == 2


def test_unknown_top_level_key_suggests_fix() -> None:
    payload = _minimal_payload()
    payload["qestions"] = payload.pop("questions")
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec(payload)
    assert "unknown key 'qestions'" in str(excinfo.value)
    assert "Did you mean 'questions'?" in str(excinfo.value)


def test_unknown_section_key_rejected() -> None:
    payload = _minimal_payload()
    payload["query"] = {"reasoning": True}
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec(payload)
    assert "at query: unknown key 'reasoning'" in str(excinfo.value)
    assert "reasoning_mode" in str(excinfo.value)


def test_errors_are_collected_not_fail_fast() -> None:
    payload = {
        "ontology": {"path": "a.yaml", "enforcement": "rigid"},
        "documents": "d/",
        "agent": {"execution_mode": "banana"},
        "models": {"default": "no-slash"},
    }
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec(payload)
    message = str(excinfo.value)
    assert "ontology.enforcement" in message
    assert "agent.execution_mode" in message
    assert "models.default" in message


@pytest.mark.parametrize(
    ("enforcement", "strict"),
    [("strict", True), ("guided", False), ("open", False)],
)
def test_enforcement_maps_to_strict_validation(enforcement: str, strict: bool) -> None:
    payload = _minimal_payload()
    payload["ontology"] = {"path": "./schema.yaml", "enforcement": enforcement}
    spec = parse_run_spec(payload)
    assert spec.enforcement == enforcement
    assert spec.strict_validation() is strict


def test_models_section_split_and_bare_string() -> None:
    payload = _minimal_payload()
    payload["models"] = {"default": "mara/MiniMax-M2.5", "indexing": "mara/MiniMax-M2"}
    spec = parse_run_spec(payload)
    assert spec.indexing_model() == "mara/MiniMax-M2"
    assert spec.query_model() == "mara/MiniMax-M2.5"
    assert spec.uses_split_models() is True

    payload["models"] = "kimi/kimi-k2.5"
    spec = parse_run_spec(payload)
    assert spec.indexing_model() == "kimi/kimi-k2.5"
    assert spec.query_model() == "kimi/kimi-k2.5"


def test_model_ref_requires_provider_prefix() -> None:
    errors: list = []
    provider, model = parse_model_ref("mara/MiniMax-M2.5", where="models.default", errors=errors)
    assert (provider, model) == ("mara", "MiniMax-M2.5")
    assert errors == []

    parse_model_ref("gpt-4o", where="models.default", errors=errors)
    assert any("provider/model" in item for item in errors)


def test_env_interpolation_with_default_and_failure(monkeypatch) -> None:
    monkeypatch.setenv("RUN_SPEC_TEST_PASSWORD", "hunter2")
    payload = _minimal_payload()
    payload["graph_password"] = "${RUN_SPEC_TEST_PASSWORD}"
    payload["database"] = "${RUN_SPEC_TEST_UNSET:-fallbackdb}"
    spec = parse_run_spec(payload)
    assert spec.graph_password == "hunter2"
    assert spec.database == "fallbackdb"

    monkeypatch.delenv("RUN_SPEC_TEST_PASSWORD")
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec(payload)
    assert "RUN_SPEC_TEST_PASSWORD is not set" in str(excinfo.value)


def test_questions_accept_strings_and_mappings() -> None:
    payload = _minimal_payload()
    payload["questions"] = [
        "Plain question?",
        {"question": "With expectation?", "expect": "Jane Park", "id": "q2"},
    ]
    spec = parse_run_spec(payload)
    assert spec.questions[0].question == "Plain question?"
    assert spec.questions[1].expect == "Jane Park"
    assert spec.questions[1].question_id == "q2"

    payload["questions"] = [{"expect": "missing question"}]
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec(payload)
    assert "requires a 'question' key" in str(excinfo.value)


def test_index_only_run_when_questions_absent() -> None:
    payload = {"ontology": "./schema.yaml", "documents": "./docs/"}
    spec = parse_run_spec(payload)
    assert spec.index_only() is True


def test_missing_required_paths() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        parse_run_spec({"questions": ["q"]})
    message = str(excinfo.value)
    assert "requires ontology" in message
    assert "requires documents" in message


def test_workspace_id_derived_from_name() -> None:
    payload = _minimal_payload()
    payload["name"] = "Filings Demo-1"
    spec = parse_run_spec(payload)
    assert spec.resolved_workspace_id() == "filings_demo_1"
    payload["workspace_id"] = "explicit_ws"
    assert parse_run_spec(payload).resolved_workspace_id() == "explicit_ws"


def test_load_run_spec_from_file(tmp_path) -> None:
    path = tmp_path / "run.yaml"
    path.write_text(
        textwrap.dedent(
            """
            ontology: ./schema.yaml
            documents: ./docs/
            questions:
              - Who is the CEO of Acme?
            """
        ).strip(),
        encoding="utf-8",
    )
    spec = load_run_spec(path)
    assert spec.name == "run"
    assert spec.source_path == str(path)


def test_load_run_spec_missing_file() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        load_run_spec("/nonexistent/run.yaml")
    assert "seocho run --init" in str(excinfo.value)


def test_graph_mapping_form_with_kind() -> None:
    spec = parse_run_spec(
        {
            "ontology": "s.yaml",
            "documents": "d/",
            "graph": {
                "kind": "dozerdb",
                "uri": "bolt://host:7687",
                "user": "u",
                "password": "pw",
                "database": "db1",
            },
        }
    )
    assert spec.graph == "bolt://host:7687"
    assert spec.graph_kind == "dozerdb"
    assert spec.resolved_graph_kind() == "dozerdb"
    assert spec.graph_user == "u"
    assert spec.graph_password == "pw"
    assert spec.database == "db1"


def test_graph_bare_string_back_compat() -> None:
    spec = parse_run_spec(
        {"ontology": "s.yaml", "documents": "d/", "graph": "bolt://host:7687"}
    )
    assert spec.graph_kind == ""
    assert spec.resolved_graph_kind() == "neo4j"  # inferred from bolt scheme
    blank = parse_run_spec({"ontology": "s.yaml", "documents": "d/"})
    assert blank.resolved_graph_kind() == "ladybug"


def test_graph_kind_coherence_errors() -> None:
    base = {"ontology": "s.yaml", "documents": "d/"}
    with pytest.raises(RunSpecError, match="requires a bolt"):
        parse_run_spec({**base, "graph": {"kind": "neo4j", "path": "./g.lbug"}})
    with pytest.raises(RunSpecError, match="embedded engine"):
        parse_run_spec({**base, "graph": {"kind": "ladybug", "uri": "bolt://h:7687"}})
    with pytest.raises(RunSpecError, match="graph.kind"):
        parse_run_spec({**base, "graph": {"kind": "postgres", "uri": "bolt://h:7687"}})
    with pytest.raises(RunSpecError, match="not both"):
        parse_run_spec(
            {**base, "graph": {"kind": "ladybug", "uri": "x", "path": "y"}}
        )


def test_vector_section_parse_and_defaults() -> None:
    base = {"ontology": "s.yaml", "documents": "d/"}
    spec = parse_run_spec({**base, "vector": {"kind": "lancedb", "uri": "./v"}})
    assert spec.uses_vector_store()
    assert spec.vector_kind() == "lancedb"
    assert spec.vector_embedding() == "fastembed"  # MARA-first default

    none_spec = parse_run_spec(base)
    assert not none_spec.uses_vector_store()

    with pytest.raises(RunSpecError, match="vector.kind"):
        parse_run_spec({**base, "vector": {"kind": "pinecone"}})
    with pytest.raises(RunSpecError, match="vector.dimension"):
        parse_run_spec({**base, "vector": {"kind": "faiss", "dimension": "big"}})
    with pytest.raises(RunSpecError, match="unknown key"):
        parse_run_spec({**base, "vector": {"kind": "faiss", "embeding": "x"}})
