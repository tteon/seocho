"""Closed-vocabulary enforcement semantics (seocho-snt)."""
from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.enforcement import (
    GUIDED,
    OPEN,
    STRICT,
    EnforcementPolicy,
    resolve_enforcement,
)
from seocho.index.extraction_engine import (
    _CLOSED_VOCABULARY_PROMPT_LINE,
    CanonicalExtractionEngine,
)
from seocho.index.pipeline import IndexingPipeline


def _ontology() -> Ontology:
    return Ontology(
        name="finance",
        nodes={
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True)}),
            "Revenue": NodeDef(
                properties={"name": P(str, unique=True)},
                broader=["FinancialMetric"],
            ),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
        },
    )


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------

def test_resolve_presets_and_legacy_flag():
    assert resolve_enforcement("strict") is STRICT
    assert resolve_enforcement("guided") is GUIDED
    assert resolve_enforcement("open") is OPEN
    assert resolve_enforcement(None) is GUIDED
    assert resolve_enforcement(None, strict_validation=True) is STRICT
    assert resolve_enforcement(STRICT) is STRICT
    assert resolve_enforcement("STRICT") is STRICT  # case-insensitive
    with pytest.raises(ValueError):
        resolve_enforcement("lenient")
    with pytest.raises(TypeError):
        resolve_enforcement(42)


def test_explicit_policy_wins_over_legacy_flag():
    assert resolve_enforcement("open", strict_validation=True) is OPEN


# ---------------------------------------------------------------------------
# Closed-vocabulary validation
# ---------------------------------------------------------------------------

def test_closed_removes_entity_exemption():
    onto = _ontology()
    data = {"nodes": [{"id": "e1", "label": "Entity", "properties": {"name": "X"}}],
            "relationships": []}
    assert onto.validate_extraction(data) == []  # default keeps the exemption
    errors = onto.validate_extraction(data, closed=True)
    assert any("unknown label 'Entity'" in e for e in errors)


def test_closed_flags_dangling_endpoints():
    onto = _ontology()
    data = {
        "nodes": [{"id": "c1", "label": "Company", "properties": {"name": "Acme"}}],
        "relationships": [
            {"source": "c1", "target": "ghost", "type": "REPORTED", "properties": {}},
        ],
    }
    assert onto.validate_extraction(data) == []  # open vocabulary: rel type known
    errors = onto.validate_extraction(data, closed=True)
    assert any("dangling target 'ghost'" in e for e in errors)


def test_closed_domain_range_conformance_via_broader_chain():
    onto = _ontology()
    # Revenue is broader->FinancialMetric, so REPORTED Company->Revenue conforms.
    ok = {
        "nodes": [
            {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
            {"id": "m1", "label": "Revenue", "properties": {"name": "FY24 revenue"}},
        ],
        "relationships": [
            {"source": "c1", "target": "m1", "type": "REPORTED", "properties": {}},
        ],
    }
    assert onto.validate_extraction(ok, closed=True) == []

    # Company as REPORTED *target* violates the declared range.
    bad = {
        "nodes": [
            {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
            {"id": "c2", "label": "Company", "properties": {"name": "Beta"}},
        ],
        "relationships": [
            {"source": "c1", "target": "c2", "type": "REPORTED", "properties": {}},
        ],
    }
    errors = onto.validate_extraction(bad, closed=True)
    assert any("expected 'FinancialMetric'" in e for e in errors)


def test_broader_chain_is_cycle_safe():
    onto = Ontology(
        name="cyclic",
        nodes={
            "A": NodeDef(properties={"name": P(str)}, broader=["B"]),
            "B": NodeDef(properties={"name": P(str)}, broader=["A"]),
        },
        relationships={"REL": RelDef(source="A", target="C")},
    )
    data = {
        "nodes": [
            {"id": "a", "label": "A", "properties": {"name": "a"}},
            {"id": "b", "label": "B", "properties": {"name": "b"}},
        ],
        "relationships": [{"source": "a", "target": "b", "type": "REL", "properties": {}}],
    }
    errors = onto.validate_extraction(data, closed=True)  # must terminate
    assert any("expected 'C'" in e for e in errors)


# ---------------------------------------------------------------------------
# Extraction engine: strict prompt line + relaxed retry gating
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, *, system, user, temperature, response_format=None,
                 reasoning_mode=None, task_hint=None):  # noqa: ANN001
        self.calls.append({"system": system, "task_hint": task_hint})
        return _FakeResponse(self.payloads.pop(0))


def test_strict_appends_constant_prompt_line_and_skips_relaxed_retry():
    llm = _FakeLLM([{"nodes": [], "relationships": []}])
    engine = CanonicalExtractionEngine(
        ontology=_ontology(), llm=llm, enforcement="strict"
    )
    out = engine.extract("Acme reported revenue.")
    # One call only: the relaxed retry (out-of-vocabulary by design) is gated.
    assert len(llm.calls) == 1
    assert llm.calls[0]["system"].endswith(_CLOSED_VOCABULARY_PROMPT_LINE)
    assert "_retry" not in out


def test_guided_keeps_relaxed_retry_and_plain_prompt():
    llm = _FakeLLM([
        {"nodes": [], "relationships": []},   # first pass: empty
        {"nodes": [{"id": "1", "label": "Company", "properties": {"name": "Acme"}}],
         "relationships": []},                # relaxed retry succeeds
    ])
    engine = CanonicalExtractionEngine(ontology=_ontology(), llm=llm)
    out = engine.extract("Acme reported revenue.")
    assert len(llm.calls) == 2
    assert _CLOSED_VOCABULARY_PROMPT_LINE not in llm.calls[0]["system"]
    assert out.get("_retry", {}).get("succeeded") is True


def test_strict_prompt_line_is_ontology_independent():
    # The extraction firewall (r=-0.76) requires the line to be constant —
    # no ontology content interpolated.
    assert "{" not in _CLOSED_VOCABULARY_PROMPT_LINE
    assert "%s" not in _CLOSED_VOCABULARY_PROMPT_LINE


# ---------------------------------------------------------------------------
# Pipeline gating
# ---------------------------------------------------------------------------

class _NullStore:
    def write(self, nodes, relationships, **kwargs):
        return {"nodes_created": len(nodes),
                "relationships_created": len(relationships), "errors": []}

    def query(self, *a, **k):
        return []


class _BoomLLM:
    def complete(self, **kwargs):
        raise RuntimeError("LLM unavailable")


def test_strict_pipeline_rejects_chunk_instead_of_heuristic_fallback():
    pipeline = IndexingPipeline(
        ontology=_ontology(), graph_store=_NullStore(), llm=_BoomLLM(),
        enforcement="strict", enable_dedup=False,
    )
    result = pipeline.index("Acme Corp reported strong revenue in 2024.")
    assert result.fallback_used is False
    assert result.skipped_chunks >= 1
    assert any("strict enforcement" in e for e in result.validation_errors)


def test_guided_pipeline_still_uses_heuristic_fallback():
    pipeline = IndexingPipeline(
        ontology=_ontology(), graph_store=_NullStore(), llm=_BoomLLM(),
        enforcement="guided", enable_dedup=False,
    )
    result = pipeline.index("Acme Corp reported strong revenue in 2024.")
    assert result.fallback_used is True


def test_strict_validation_property_backcompat_roundtrip():
    pipeline = IndexingPipeline(
        ontology=_ontology(), graph_store=_NullStore(), llm=_BoomLLM(),
        strict_validation=True,
    )
    assert pipeline.enforcement is STRICT
    assert pipeline.strict_validation is True
    pipeline.strict_validation = False
    assert pipeline.enforcement is GUIDED
    assert pipeline._graph_extraction.enforcement is GUIDED


def test_strict_validation_setter_noop_preserves_custom_policy():
    pipeline = IndexingPipeline(
        ontology=_ontology(), graph_store=_NullStore(), llm=_BoomLLM(),
        enforcement="open",
    )
    pipeline.strict_validation = False  # boolean view already False -> no-op
    assert pipeline.enforcement is OPEN


def test_open_pipeline_annotates_out_of_ontology():
    nodes = [
        {"id": "c1", "label": "Company", "properties": {"name": "Acme"}},
        {"id": "x1", "label": "Spaceship", "properties": {"name": "Falcon"}},
    ]
    rels = [{"source": "c1", "target": "x1", "type": "PILOTS", "properties": {}}]
    pipeline = IndexingPipeline(
        ontology=_ontology(), graph_store=_NullStore(), llm=_BoomLLM(),
        enforcement="open",
    )
    pipeline._annotate_out_of_ontology(nodes, rels)
    assert "_out_of_ontology" not in nodes[0]["properties"]
    assert nodes[1]["properties"]["_out_of_ontology"] is True
    assert rels[0]["properties"]["_out_of_ontology"] is True


def test_policy_is_frozen():
    with pytest.raises(Exception):
        STRICT.mode = "other"  # type: ignore[misc]
    assert isinstance(STRICT, EnforcementPolicy)
