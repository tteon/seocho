"""seocho-snt: closed-vocabulary enforcement semantics (strict/guided/open).

Covers the EnforcementPolicy presets, closed validation in Ontology,
strict gating in the extraction engine and pipeline, the strict_validation
floor, open-mode annotation, and the agent-design / run-spec wiring.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from seocho.agent_design import AgentDesignSpec
from seocho.index.enforcement import EnforcementPolicy, annotate_out_of_ontology
from seocho.index.extraction_engine import CanonicalExtractionEngine
from seocho.ontology import NodeDef, Ontology, P, RelDef


def _ontology() -> Ontology:
    return Ontology(
        name="enforcement_demo",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Bank": NodeDef(
                properties={"name": P(str, unique=True)},
                broader=["Company"],
            ),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "CEO_OF": RelDef(source="Person", target="Company"),
            "MENTIONS": RelDef(source="Any", target="Any"),
        },
    )


# ---------------------------------------------------------------------------
# EnforcementPolicy presets
# ---------------------------------------------------------------------------


def test_policy_presets() -> None:
    strict = EnforcementPolicy.from_mode("strict")
    assert strict.prompt_strict
    assert not strict.allow_relaxed_retry
    assert not strict.allow_entity_fallback
    assert not strict.allow_heuristic_fallback
    assert strict.violation_action == "reject"
    assert strict.closed_validation
    assert not strict.annotate_out_of_ontology

    guided = EnforcementPolicy.from_mode("guided")
    assert not guided.prompt_strict
    assert guided.allow_relaxed_retry
    assert guided.violation_action == "warn"
    assert EnforcementPolicy.from_mode("") == guided
    assert EnforcementPolicy.from_mode(None) == guided

    open_mode = EnforcementPolicy.from_mode("open")
    assert open_mode.annotate_out_of_ontology
    assert open_mode.violation_action == "warn"

    with pytest.raises(ValueError, match="Unknown enforcement mode"):
        EnforcementPolicy.from_mode("rigid")


# ---------------------------------------------------------------------------
# Ontology closed validation
# ---------------------------------------------------------------------------


def test_closed_validation_drops_entity_exemption() -> None:
    onto = _ontology()
    data = {"nodes": [{"id": "x", "label": "Entity", "properties": {"name": "X"}}],
            "relationships": []}
    assert onto.validate_extraction(data) == []
    closed_errors = onto.validate_extraction(data, closed=True)
    assert any("unknown label 'Entity'" in e for e in closed_errors)


def test_closed_validation_flags_dangling_endpoints() -> None:
    onto = _ontology()
    data = {
        "nodes": [{"id": "p1", "label": "Person", "properties": {"name": "Jane"}}],
        "relationships": [
            {"source": "p1", "target": "ghost", "type": "CEO_OF", "properties": {}},
        ],
    }
    assert onto.validate_extraction(data) == []  # open: endpoints unchecked
    closed_errors = onto.validate_extraction(data, closed=True)
    assert any("does not reference an extracted node" in e for e in closed_errors)


def test_closed_validation_checks_domain_range_with_broader_subsumption() -> None:
    onto = _ontology()
    # Bank broader-chains to Company, so Bank is a valid CEO_OF target.
    ok = {
        "nodes": [
            {"id": "p1", "label": "Person", "properties": {"name": "Jane"}},
            {"id": "b1", "label": "Bank", "properties": {"name": "Acme Bank"}},
        ],
        "relationships": [
            {"source": "p1", "target": "b1", "type": "CEO_OF", "properties": {}},
        ],
    }
    assert onto.validate_extraction(ok, closed=True) == []

    # Person as CEO_OF target violates the declared range.
    bad = {
        "nodes": [
            {"id": "p1", "label": "Person", "properties": {"name": "Jane"}},
            {"id": "p2", "label": "Person", "properties": {"name": "Kim"}},
        ],
        "relationships": [
            {"source": "p1", "target": "p2", "type": "CEO_OF", "properties": {}},
        ],
    }
    errors = onto.validate_extraction(bad, closed=True)
    assert any("declares target 'Company'" in e for e in errors)


def test_closed_validation_any_wildcard_endpoints() -> None:
    onto = _ontology()
    data = {
        "nodes": [
            {"id": "p1", "label": "Person", "properties": {"name": "Jane"}},
            {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
        ],
        "relationships": [
            {"source": "c1", "target": "p1", "type": "MENTIONS", "properties": {}},
        ],
    }
    assert onto.validate_extraction(data, closed=True) == []


def test_validate_with_shacl_threads_closed() -> None:
    onto = _ontology()
    data = {"nodes": [{"id": "x", "label": "Entity", "properties": {"name": "X"}}],
            "relationships": []}
    assert onto.validate_with_shacl(data) == []
    assert onto.validate_with_shacl(data, closed=True)


def test_sanitize_label_strict_raises_instead_of_entity() -> None:
    onto = _ontology()
    assert onto.sanitize_label("Spaceship") == "Entity"
    assert onto.is_valid_label("Entity")
    assert not onto.is_valid_label("Entity", allow_entity_fallback=False)
    with pytest.raises(ValueError, match="strict enforcement"):
        onto.sanitize_label("Spaceship", allow_entity_fallback=False)


def test_annotate_out_of_ontology_marks_unknown_elements() -> None:
    onto = _ontology()
    nodes = [
        {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
        {"id": "x1", "label": "Spaceship", "properties": {"name": "X"}},
    ]
    rels = [
        {"source": "c1", "target": "x1", "type": "LAUNCHED", "properties": {}},
        {"source": "x1", "target": "c1", "type": "MENTIONS", "properties": {}},
    ]
    count = annotate_out_of_ontology(onto, nodes, rels)
    assert count == 2
    assert "_out_of_ontology" not in nodes[0]["properties"]
    assert nodes[1]["properties"]["_out_of_ontology"] == "true"
    assert rels[0]["properties"]["_out_of_ontology"] == "true"
    assert "_out_of_ontology" not in rels[1]["properties"]


# ---------------------------------------------------------------------------
# Extraction engine gating
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.text = ""

    def json(self) -> Dict[str, Any]:
        return dict(self._payload)


class _RecordingLLM:
    """Captures every completion call the engine makes."""

    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: List[Dict[str, str]] = []

    def complete(self, *, system: str, user: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"system": system, "user": user})
        payload = self.payloads.pop(0) if self.payloads else {"nodes": [], "relationships": []}
        return _FakeResponse(payload)


def test_strict_disables_relaxed_retry_and_adds_constant_prompt_line() -> None:
    llm = _RecordingLLM([{"nodes": [], "relationships": []}])
    engine = CanonicalExtractionEngine(ontology=_ontology(), llm=llm, enforcement="strict")
    result = engine.extract("Nothing in vocabulary here.")
    assert result == {"nodes": [], "relationships": []}
    assert len(llm.calls) == 1  # no relaxed retry call
    assert "Closed vocabulary" in llm.calls[0]["system"]
    assert "Never use the generic 'Entity' label" in llm.calls[0]["system"]


def test_guided_keeps_relaxed_retry_and_plain_prompt() -> None:
    llm = _RecordingLLM([
        {"nodes": [], "relationships": []},
        {"nodes": [{"id": "c1", "label": "Company", "properties": {"name": "Acme"}}],
         "relationships": []},
    ])
    engine = CanonicalExtractionEngine(ontology=_ontology(), llm=llm, enforcement="guided")
    result = engine.extract("Acme did things.")
    assert len(llm.calls) == 2  # relaxed retry happened
    assert "Closed vocabulary" not in llm.calls[0]["system"]
    assert result["nodes"]


def test_strict_prompt_line_is_ontology_independent() -> None:
    """Extraction-firewall guard: the strict line must not derive from the
    ontology (byte-identical across ontologies)."""
    other = Ontology(
        name="other",
        nodes={"Planet": NodeDef(properties={"name": P(str, unique=True)},
                                 broader=["CelestialBody"], same_as="schema:Planet")},
        relationships={},
    )
    lines = []
    for onto in (_ontology(), other):
        llm = _RecordingLLM([{"nodes": [], "relationships": []}])
        CanonicalExtractionEngine(ontology=onto, llm=llm, enforcement="strict").extract("x")
        system = llm.calls[0]["system"]
        start = system.index("Closed vocabulary")
        lines.append(system[start:])
    assert lines[0] == lines[1]
    assert "broader" not in lines[0]
    assert "same_as" not in lines[0]


# ---------------------------------------------------------------------------
# Pipeline gating
# ---------------------------------------------------------------------------


class _NullStore:
    def write(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"nodes_created": 0, "relationships_created": 0, "errors": []}

    def ensure_constraints(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"success": 0, "errors": []}


def _pipeline(enforcement: str, llm: Any):
    from seocho.index.pipeline import IndexingPipeline

    return IndexingPipeline(
        ontology=_ontology(),
        graph_store=_NullStore(),
        llm=llm,
        enforcement=enforcement,
    )


class _ExplodingLLM:
    def complete(self, **kwargs: Any) -> Any:
        raise RuntimeError("backend down")


def test_strict_pipeline_records_error_instead_of_heuristic_fallback() -> None:
    pipeline = _pipeline("strict", _ExplodingLLM())
    result = pipeline.index("Acme Corp launched AcmeCloud.", source_id="s1")
    assert result.fallback_used is False
    assert result.total_nodes == 0
    assert any("strict enforcement" in e for e in result.write_errors)
    assert result.skipped_chunks >= 1


def test_guided_pipeline_keeps_heuristic_fallback() -> None:
    pipeline = _pipeline("guided", _ExplodingLLM())
    result = pipeline.index("Acme Corp launched AcmeCloud.", source_id="s1")
    assert result.fallback_used is True
    assert len(result.nodes) > 0  # Entity/MENTIONS structure manufactured


def test_strict_pipeline_skips_empty_extraction_quietly() -> None:
    llm = _RecordingLLM([{"nodes": [], "relationships": []}])
    pipeline = _pipeline("strict", llm)
    result = pipeline.index("Out of vocabulary text.", source_id="s1")
    assert result.fallback_used is False
    assert result.write_errors == []
    assert result.skipped_chunks == 1


def test_strict_enforcement_forces_strict_validation_floor() -> None:
    from seocho.index.ingestion_facade import IngestRequest, IngestionFacade

    llm = _RecordingLLM([{"nodes": [], "relationships": []}])
    pipeline = _pipeline("strict", llm)
    assert pipeline.strict_validation is True

    facade = IngestionFacade(pipeline)
    facade.ingest(
        IngestRequest(content="text", workspace_id="w", strict_validation=False)
    )
    # the per-request False must not have downgraded the strict floor
    assert pipeline.strict_validation is True


def test_open_pipeline_annotates_out_of_ontology_nodes() -> None:
    llm = _RecordingLLM([
        {
            "nodes": [
                {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
                {"id": "x1", "label": "Spaceship", "properties": {"name": "Falcon"}},
            ],
            "relationships": [],
        }
    ])
    pipeline = _pipeline("open", llm)
    result = pipeline.index("Acme launched Falcon.", source_id="s1")
    by_label = {n["label"]: n for n in result.nodes}
    assert "_out_of_ontology" not in by_label["Company"].get("properties", {})
    assert by_label["Spaceship"]["properties"]["_out_of_ontology"] == "true"
    # open mode warns, never rejects
    assert result.skipped_chunks == 0


def test_strict_pipeline_rejects_chunk_with_unknown_labels() -> None:
    llm = _RecordingLLM([
        {
            "nodes": [{"id": "x1", "label": "Spaceship", "properties": {"name": "Falcon"}}],
            "relationships": [],
        }
    ])
    pipeline = _pipeline("strict", llm)
    result = pipeline.index("Falcon launch.", source_id="s1")
    assert result.skipped_chunks == 1
    assert result.total_nodes == 0
    assert any("unknown label 'Spaceship'" in e for e in result.validation_errors)


# ---------------------------------------------------------------------------
# Agent design wiring
# ---------------------------------------------------------------------------


def _design_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": "demo",
        "pattern": "memory_tool_use",
        "ontology": {"profile": "demo"},
    }
    payload.update(overrides)
    return payload


def test_agent_design_parses_enforcement_and_derives_validation_action() -> None:
    spec = AgentDesignSpec.from_dict(
        _design_payload(ontology={"profile": "demo", "enforcement": "strict"})
    )
    config = spec.to_agent_config()
    assert config.ontology_enforcement == "strict"
    assert config.validation_on_fail == "reject"
    assert config.extra["agent_design_ontology"]["enforcement"] == "strict"

    open_spec = AgentDesignSpec.from_dict(
        _design_payload(ontology={"profile": "demo", "enforcement": "open"})
    )
    open_config = open_spec.to_agent_config()
    assert open_config.ontology_enforcement == "open"
    assert open_config.validation_on_fail == "warn"

    default_config = AgentDesignSpec.from_dict(_design_payload()).to_agent_config()
    assert default_config.ontology_enforcement == "guided"


def test_agent_design_rejects_incoherent_enforcement_combos() -> None:
    with pytest.raises(ValueError, match="incoherent"):
        AgentDesignSpec.from_dict(
            _design_payload(
                ontology={"profile": "demo", "enforcement": "strict"},
                indexing={"validation_on_fail": "warn"},
            )
        )
    with pytest.raises(ValueError, match="incoherent"):
        AgentDesignSpec.from_dict(
            _design_payload(
                ontology={"profile": "demo", "enforcement": "open"},
                indexing={"validation_on_fail": "reject"},
            )
        )
    # strict + retry stays coherent
    AgentDesignSpec.from_dict(
        _design_payload(
            ontology={"profile": "demo", "enforcement": "strict"},
            indexing={"validation_on_fail": "retry"},
        )
    )
    with pytest.raises(ValueError, match="enforcement must be one of"):
        AgentDesignSpec.from_dict(
            _design_payload(ontology={"profile": "demo", "enforcement": "rigid"})
        )


# ---------------------------------------------------------------------------
# Run-spec / e2e wiring
# ---------------------------------------------------------------------------


def test_run_spec_enforcement_overrides_design_only_when_explicit(tmp_path) -> None:
    import textwrap

    from seocho import e2e
    from seocho.run_spec import parse_run_spec

    design = tmp_path / "design.yaml"
    design.write_text(
        textwrap.dedent(
            """
            name: strict-design
            pattern: memory_tool_use
            ontology:
              profile: demo
              enforcement: strict
            """
        ).strip(),
        encoding="utf-8",
    )

    base = {
        "ontology": "schema.yaml",
        "documents": "docs",
        "agent": {"design": "design.yaml"},
    }
    # implicit guided default must NOT override the design's strict
    spec = parse_run_spec(dict(base), source_path=str(tmp_path / "run.yaml"))
    assert spec.enforcement_set is False
    config = e2e.build_agent_config(spec)
    assert config.ontology_enforcement == "strict"
    assert config.validation_on_fail == "reject"

    # explicit open overrides the design
    explicit = dict(base)
    explicit["ontology"] = {"path": "schema.yaml", "enforcement": "open"}
    spec = parse_run_spec(explicit, source_path=str(tmp_path / "run.yaml"))
    assert spec.enforcement_set is True
    config = e2e.build_agent_config(spec)
    assert config.ontology_enforcement == "open"


def test_run_spec_inline_enforcement_lands_on_agent_config() -> None:
    from seocho import e2e
    from seocho.run_spec import parse_run_spec

    spec = parse_run_spec(
        {
            "ontology": {"path": "s.yaml", "enforcement": "strict"},
            "documents": "docs",
        }
    )
    config = e2e.build_agent_config(spec)
    assert config.ontology_enforcement == "strict"
    assert config.validation_on_fail == "reject"
