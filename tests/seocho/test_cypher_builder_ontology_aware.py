"""Regression tests for the ontology-aware financial-metric lookup.

Locks today's fixes in cypher_builder._financial_metric_lookup /
_metric_anchor_labels:
  - metric/anchor labels derived from the ONTOLOGY (FIBO LegalEntity/Revenue),
    not hardcoded :Company/:FinancialMetric
  - metric_aliases / metric_scope_tokens are SOFT (ORDER BY), never hard WHERE
    filters (a question-stopword token must not eliminate rows)
  - labels are passed as parameters, never string-interpolated (cypher-safety §8)
  - anchor matches by ticker as well as name
No live Neo4j.
"""
from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.query.cypher_builder import CypherBuilder


def _fibo_ontology() -> Ontology:
    """A FIBO-style graph: LegalEntity reports value-bearing metric subclasses."""
    return Ontology(
        name="fibo_be_ind",
        graph_model="lpg",
        nodes={
            "LegalEntity": NodeDef(properties={"name": P(str, unique=True), "ticker": P(str)}),
            "Revenue": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "period": P(str)}),
            "NetIncome": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "period": P(str)}),
        },
        relationships={
            "REPORTED_METRIC": RelDef(source="LegalEntity", target="Revenue",
                                      description="entity reported a metric"),
        },
    )


def _lookup(builder: CypherBuilder):
    return builder._financial_metric_lookup(
        anchor_entity="JKHY", metric_name="net sales",
        metric_aliases=["sales", "revenue"],
        metric_scope_tokens=["trend", "gross"],   # question-stopword-ish
        years=["2023"], workspace_id="ws1", limit=8,
    )


def test_metric_anchor_labels_derived_from_ontology() -> None:
    metric_labels, anchor_labels = CypherBuilder(_fibo_ontology())._metric_anchor_labels()
    # value-bearing subclasses become metric labels (plus legacy bases)
    assert "Revenue" in metric_labels and "NetIncome" in metric_labels
    assert "FinancialMetric" in metric_labels  # legacy base kept
    # the relationship source becomes an anchor label (plus legacy)
    assert "LegalEntity" in anchor_labels


def test_aliases_and_scope_tokens_are_soft_order_by_not_where() -> None:
    cypher, params = _lookup(CypherBuilder(_fibo_ontology()))
    where_part, _, order_part = cypher.partition("ORDER BY")
    # tokens/aliases must NOT gate the WHERE (they cannot eliminate rows)...
    assert "$metric_aliases" not in where_part
    assert "$metric_scope_tokens" not in where_part
    # ...they only influence ranking
    assert "$metric_aliases" in order_part
    assert "$metric_scope_tokens" in order_part


def test_labels_passed_as_params_not_interpolated() -> None:
    cypher, params = _lookup(CypherBuilder(_fibo_ontology()))
    # labels travel as parameters, never as :Revenue interpolated into the query
    assert "$metric_labels" in cypher and "$anchor_labels" in cypher
    assert ":Revenue" not in cypher and ":LegalEntity" not in cypher
    assert "Revenue" in params["metric_labels"]
    assert "LegalEntity" in params["anchor_labels"]


def test_anchor_matches_ticker_branch() -> None:
    cypher, params = _lookup(CypherBuilder(_fibo_ontology()))
    assert "c.ticker" in cypher
    assert params["anchor"] == "JKHY"
    # period OR year matching present (FY period vs bare year)
    assert "m.period" in cypher


# ---------------------------------------------------------------------------
# _count: ontology-declared endpoints are authoritative for anchor vs counted
# ---------------------------------------------------------------------------

def _carbon_like_ontology() -> Ontology:
    """Distinct endpoint labels: Project -LOCATED_IN-> HostCountry."""
    return Ontology(
        name="carbon_like",
        graph_model="lpg",
        nodes={
            "Project": NodeDef(properties={"registry_id": P(str, unique=True), "name": P(str)}),
            "HostCountry": NodeDef(properties={"country_iso": P(str, unique=True), "name": P(str, index=True)}),
        },
        relationships={
            "LOCATED_IN": RelDef(source="Project", target="HostCountry",
                                 description="project located in country"),
        },
    )


def test_count_filter_entity_anchors_on_declared_target() -> None:
    """Regression: "How many projects are in Vietnam" produced
    (src:HostCountry)-[:LOCATED_IN]->(anchor:Project) with
    anchor.registry_id = 'Vietnam' — declared-direction reversed AND the filter
    equality on the counted label, so the count was always 0. With distinct
    endpoint labels the declaration decides: counted = source, anchor = target,
    regardless of how the plan filled anchor_label/target_label."""
    builder = CypherBuilder(_carbon_like_ontology())
    for anchor_label, target_label in (("HostCountry", "Project"), ("Project", "HostCountry")):
        cypher, params = builder.build(
            intent="count", anchor_entity="Vietnam",
            anchor_label=anchor_label, target_label=target_label,
            relationship_type="LOCATED_IN", workspace_id="ws",
        )
        assert "(src:`Project`)-[:`LOCATED_IN`]->(anchor:`HostCountry`)" in cypher
        assert "anchor:`Project`" not in cypher
        assert "Vietnam" in list(params.values())


def test_count_same_label_relationship_keeps_in_degree() -> None:
    """FinBench-style Account -TRANSFER-> Account keeps the existing
    anchor-as-arrow-head behaviour (no override for same-label endpoints)."""
    ontology = Ontology(
        name="fin", graph_model="lpg",
        nodes={"Account": NodeDef(properties={"id": P(str, unique=True)})},
        relationships={"TRANSFER": RelDef(source="Account", target="Account")},
    )
    cypher, _ = CypherBuilder(ontology).build(
        intent="count", anchor_entity="42",
        anchor_label="Account", target_label="Account",
        relationship_type="TRANSFER", workspace_id="ws",
    )
    assert "(src:`Account`)-[:`TRANSFER`]->(anchor:`Account`)" in cypher
