"""EXPLAIN-based plan signals, and the repair hint they produce.

Shapes verified against a live DozerDB 5.26.3: `EXPLAIN MATCH (n:X) WHERE
n.name='x'` yields NodeByLabelScan and dropping the label yields AllNodesScan,
both without touching data. Operator names arrive suffixed as
`NodeByLabelScan@neo4j`, which is why matching is by substring.

The point of EXPLAIN over PROFILE is that it costs nothing, so a gate can run on
every query instead of a sample — and the seek/scan distinction, which is what
predicts behaviour as the graph grows, is already decided at plan time.
"""

from __future__ import annotations

from seocho import NodeDef, Ontology, P
from seocho.query.plan_quality import repair_hint, summarize_plan


def _plan(*operators: str) -> dict:
    """Nest operators outermost-first, as Neo4j returns them."""
    node: dict = {"operatorType": operators[-1], "args": {"EstimatedRows": 41000.0},
                  "children": []}
    for op in reversed(operators[:-1]):
        node = {"operatorType": op, "args": {"EstimatedRows": 0.5}, "children": [node]}
    return node


def _ontology() -> Ontology:
    return Ontology(name="probe", nodes={
        "Decision": NodeDef(properties={"name": P(str, unique=True), "note": P(str)})})


def test_a_label_scan_is_not_sargable():
    summary = summarize_plan(_plan("ProduceResults@neo4j", "Filter@neo4j",
                                   "NodeByLabelScan@neo4j"))
    assert summary["sargable"] is False
    assert summary["scans"] == ["NodeByLabelScan@neo4j"]
    assert summary["estimated_rows"] == 41000.0


def test_an_index_seek_is_sargable():
    summary = summarize_plan(_plan("ProduceResults@neo4j", "NodeUniqueIndexSeek@neo4j"))
    assert summary["sargable"] is True
    assert summary["scans"] == []


def test_one_scan_anywhere_fails_the_whole_plan():
    """Deliberately strict: a single growing component is the thing to alert on."""
    summary = summarize_plan(_plan("ProduceResults@neo4j", "NodeIndexSeek@neo4j",
                                   "AllNodesScan@neo4j"))
    assert summary["seeks"] and summary["scans"]
    assert summary["sargable"] is False


def test_an_absent_plan_is_reported_as_unavailable():
    assert summarize_plan(None) == {"available": False, "source": "explain"}


def test_a_sargable_plan_produces_no_hint():
    summary = summarize_plan(_plan("ProduceResults@neo4j", "NodeUniqueIndexSeek@neo4j"))
    assert repair_hint(summary, _ontology()) is None


def test_the_hint_names_an_indexed_property_from_the_ontology():
    """The schema is the evidence — the model is not asked to guess."""
    summary = summarize_plan(_plan("ProduceResults@neo4j", "NodeByLabelScan@neo4j"))
    hint = repair_hint(summary, _ontology())
    assert "Decision.name" in hint
    assert "index seek is available" in hint
    assert "NodeByLabelScan" in hint


def test_the_hint_says_so_when_no_seek_is_available():
    """Telling the model to anchor on an index that does not exist is worse
    than telling it there is none."""
    ontology = Ontology(name="flat", nodes={
        "Decision": NodeDef(properties={"note": P(str)})})
    hint = repair_hint(summarize_plan(_plan("ProduceResults@neo4j",
                                            "AllNodesScan@neo4j")), ontology)
    assert "no unique property" in hint
    assert "narrow the match" in hint


def test_the_gate_is_off_unless_asked(monkeypatch):
    """It changes WHEN repair fires, so it stays behind a flag until measured."""
    from seocho.local_engine import _LocalEngine

    monkeypatch.delenv("SEOCHO_PLAN_GATE", raising=False)
    engine = object.__new__(_LocalEngine)

    class _Executor:
        def explain(self, plan):
            raise AssertionError("must not be consulted when the gate is off")

    assert engine._plan_repair_hint("MATCH (n) RETURN n", {}, "neo4j",
                                    executor=_Executor()) is None


def test_a_planning_failure_never_propagates(monkeypatch):
    """A probe on the read path must not be able to fail a user's query."""
    from seocho.local_engine import _LocalEngine

    monkeypatch.setenv("SEOCHO_PLAN_GATE", "1")
    engine = object.__new__(_LocalEngine)

    class _Boom:
        def explain(self, plan):
            raise RuntimeError("EXPLAIN unsupported on this backend")

    assert engine._plan_repair_hint("MATCH (n) RETURN n", {}, "neo4j",
                                    executor=_Boom()) is None


def test_a_backend_without_explain_yields_no_signal():
    """`None` means no signal, and must not read as a good plan or a bad one."""
    from seocho.query.contracts import QueryPlan
    from seocho.query.executor import GraphQueryExecutor

    class _Store:
        pass

    executor = GraphQueryExecutor(graph_store=_Store(), database="neo4j")
    assert executor.explain(QueryPlan(question="", cypher="MATCH (n) RETURN n",
                                      params={})) is None
    assert summarize_plan(None)["available"] is False
