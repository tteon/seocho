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
    # value-bearing subclasses become metric labels
    assert "Revenue" in metric_labels and "NetIncome" in metric_labels
    # the relationship source becomes an anchor label
    assert "LegalEntity" in anchor_labels


def test_undeclared_legacy_labels_are_not_injected() -> None:
    """A generic builder must not assume a financial domain.

    `FinancialMetric`, `MonetaryAmount`, `Company` and `Entity` used to be
    appended unconditionally, so an ontology with no financial concepts was
    still told they were valid labels and built Cypher against classes its
    graph does not contain. The fixture here declares `LegalEntity`, `Revenue`
    and `NetIncome` and nothing else.

    An ontology that genuinely has legacy nodes under those names should
    declare them, or alias them on the class that replaced them; the builder
    reads the ontology rather than guessing a domain.
    """
    metric_labels, anchor_labels = CypherBuilder(_fibo_ontology())._metric_anchor_labels()
    for undeclared in ("FinancialMetric", "MonetaryAmount"):
        assert undeclared not in metric_labels, undeclared
    for undeclared in ("Company", "Entity"):
        assert undeclared not in anchor_labels, undeclared


def test_declared_legacy_labels_are_still_honoured() -> None:
    """Removing the guess must not remove the capability."""
    from seocho import NodeDef, Ontology, P

    ontology = Ontology(
        name="legacy",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True),
                                                   "value": P(str)}),
        },
    )
    metric_labels, anchor_labels = CypherBuilder(ontology)._metric_anchor_labels()
    assert "FinancialMetric" in metric_labels
    assert "Company" in anchor_labels


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
