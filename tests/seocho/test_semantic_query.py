"""Unit tests for the semantic-layer query lane (ADR-0103 S4) — fakes, no net.

Drives semantic_answer with a fake LLM (returns canned QuerySlots JSON) and a
fake graph_store (canned probe + lookup rows), asserting the STRUCTURED happy
path formats a deterministic answer and that CLARIFY/NARRATIVE/empty routes
return answer=None so the caller falls through.
"""

from __future__ import annotations

from seocho.query.semantic_query import format_observation, semantic_answer
from seocho.semantic_layer import default_registry, default_resolver


class _LLM:
    def __init__(self, slots_json: str):
        self._json = slots_json

    def complete(self, *, system, user, **kw):
        class _R:
            text = self._json
        return _R()


class _Graph:
    """Fake graph: probe returns available periods; lookup returns a value row."""
    def __init__(self, periods, value=None):
        self._periods = periods
        self._value = value

    def query(self, cypher, params=None, database="neo4j"):
        if "collect(DISTINCT" in cypher:                 # arbiter probe
            return [{"periods": list(self._periods)}]
        if self._value is None:                          # lookup, no row
            return []
        return [{"value": self._value, "unit": "USD", "period": "fiscal:2024:FY"}]


_REV_2024 = ('{"intent":"metric_lookup","metric_surface":"total revenue",'
             '"entity_surface":"Apple Inc.","period":"FY2024"}')


def _kw(graph):
    return dict(llm=None, graph_store=graph, database="db", workspace_id="ws",
               registry=default_registry(), resolver=default_resolver())


def test_format_observation_usd():
    assert format_observation({"value": 391035000000.0, "unit": "USD",
                               "period": "fiscal:2024:FY"}) == "$391,035 million (fiscal:2024:FY)"


def test_structured_happy_path_returns_formatted_answer():
    kw = _kw(_Graph(periods=["fiscal:2024:FY"], value=391035000000.0))
    kw["llm"] = _LLM(_REV_2024)
    sr = semantic_answer("What was Apple Inc.'s total revenue for fiscal year 2024?", **kw)
    assert sr.route == "STRUCTURED"
    assert sr.answer == "$391,035 million (fiscal:2024:FY)"
    assert sr.slots.is_fully_resolved


def test_structured_but_empty_execution_demotes_to_narrative():
    # arbiter says STRUCTURED (period present in probe) but the lookup returns
    # no row -> answer None, route demoted so the caller falls through.
    kw = _kw(_Graph(periods=["fiscal:2024:FY"], value=None))
    kw["llm"] = _LLM(_REV_2024)
    sr = semantic_answer("...", **kw)
    assert sr.answer is None and sr.route == "NARRATIVE"


def test_period_absent_routes_clarify_no_answer():
    kw = _kw(_Graph(periods=["fiscal:2023:FY"], value=1.0))   # 2024 not present
    kw["llm"] = _LLM(_REV_2024)
    sr = semantic_answer("...", **kw)
    assert sr.answer is None and sr.route == "CLARIFY"


def test_out_of_vocab_routes_narrative_no_answer():
    kw = _kw(_Graph(periods=["fiscal:2024:FY"], value=1.0))
    kw["llm"] = _LLM('{"intent":"metric_lookup","metric_surface":"litigation risk",'
                     '"entity_surface":"Apple Inc.","period":"FY2024"}')
    sr = semantic_answer("...", **kw)
    assert sr.answer is None and sr.route == "NARRATIVE"


# ---- v2: multi-ontology routing in the lane ---------------------------------

def test_semantic_answer_multi_ontology_picks_finance_and_answers():
    from seocho.query.arbiter import OntologyManifest
    from seocho.semantic_layer import (
        ConceptRegistry, EntityResolver, MetricConcept, default_resolver,
    )
    finance = OntologyManifest("finance", default_registry(), default_resolver())
    clinical = OntologyManifest(
        "clinical",
        ConceptRegistry((MetricConcept("metric:PatientCount", "Patient Count",
                                       ("admissions",), "count"),)),
        EntityResolver(),
    )
    graph = _Graph(periods=["fiscal:2024:FY"], value=391035000000.0)
    sr = semantic_answer(
        "What was Apple Inc.'s total revenue for fiscal year 2024?",
        llm=_LLM(_REV_2024), graph_store=graph, database="db", workspace_id="ws",
        manifests=[clinical, finance],   # order shouldn't matter; finance must win
    )
    assert sr.route == "STRUCTURED"
    assert sr.answer == "$391,035 million (fiscal:2024:FY)"
    assert sr.hint.ontology_id == "finance"


# ---- H3: operational route policy (clarification) ---------------------------

def test_clarification_message_offers_available_periods():
    from seocho.query.arbiter import ArbiterHint, CLARIFY
    from seocho.query.semantic_query import clarification_message
    hint = ArbiterHint(route=CLARIFY, missing_slots=(),
                       available_periods=("fiscal:2024:FY", "fiscal:2023:FY"))
    msg = clarification_message(hint)
    assert "fiscal year" in msg.lower()
    assert "FY2023" in msg and "FY2024" in msg


def test_clarification_message_period_unspecified_and_entity():
    from seocho.query.arbiter import ArbiterHint, CLARIFY
    from seocho.query.semantic_query import clarification_message
    assert "fiscal year" in clarification_message(
        ArbiterHint(route=CLARIFY, missing_slots=("period",))).lower()
    assert "company" in clarification_message(
        ArbiterHint(route=CLARIFY, missing_slots=("entity",))).lower()


def test_lane_clarify_route_returns_no_answer_for_caller_to_surface():
    # period present in question but absent in graph -> CLARIFY, answer None
    kw = _kw(_Graph(periods=["fiscal:2023:FY"], value=1.0))   # 2024 not present
    kw["llm"] = _LLM(_REV_2024)
    sr = semantic_answer("...", **kw)
    assert sr.route == "CLARIFY" and sr.answer is None
    from seocho.query.semantic_query import clarification_message
    assert "FY2023" in clarification_message(sr.hint)         # offers what IS there
