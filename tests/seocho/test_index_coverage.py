"""Indexes must cover the properties the write and read paths actually use.

Before this, three property sets were disjoint:

  the ontology indexed   whatever was declared `unique` / `index`
  the writer MERGEd on   `id` (+`_workspace_id` since review #6: MERGE (n:L {id: row.id, _workspace_id: $ws}))
  the retriever filters  the display property and `_workspace_id`

So no index served any predicate a correct query would use. The consequence is
not only slowness: a plan grader reads that as "the LLM writes unsargable
Cypher" when the truth is "no index exists for the predicate", which attributes
a data-layer defect to the generation stage — the single biggest threat to a
stage-attribution study.

Every statement here was accepted by a live DozerDB 5.26.3 (7 of 7), and the
planner was confirmed to use them.
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="t",
        nodes={
            "Person": NodeDef(
                description="p",
                properties={"name": P(str, unique=True), "age": P(int)},
                identity_keys=["name"],
            ),
            "Step": NodeDef(
                description="s",
                properties={"name": P(str), "procedure": P(str)},
                identity_keys=["name", "procedure"],
            ),
            "Decision": NodeDef(
                description="d",
                properties={"name": P(str), "status": P(str)},
            ),
        },
    )


def test_merge_key_is_indexed_for_every_label(ontology):
    """`MERGE (n:L {id: ...})` without an index is a label scan per write, and
    two per relationship — ingest is quadratic."""
    statements = ontology.to_cypher_constraints()
    for label in ontology.nodes:
        assert any(
            f"FOR (n:{label}) ON (n.id)" in s for s in statements
        ), f"{label} has no index on the property the writer merges on"


def test_tenancy_is_composite_with_the_anchor(ontology):
    """`_workspace_id` alone is the lowest-cardinality property in the store, so
    a seek on it returns most of the label. Composite with the identity key it
    becomes O(this tenant's matching entities)."""
    statements = ontology.to_cypher_constraints()
    assert any(
        "ON (n._workspace_id, n.name)" in s and "(n:Person)" in s
        for s in statements
    )
    # Tenancy must come FIRST in the composite, or the prefix seek does not help.
    composites = [s for s in statements if "_workspace_id" in s]
    assert composites
    for statement in composites:
        assert "ON (n._workspace_id," in statement


def test_single_identity_key_gets_a_unique_constraint(ontology):
    """The pre-existing gap: the composite branch required >1 key and the
    per-property branch skipped anything in `identity`, so a class with exactly
    one identity key got no constraint at all — for the very property the writer
    dedupes on."""
    statements = ontology.to_cypher_constraints()
    assert any(
        "constraint_Person_identity_unique" in s and "REQUIRE n.name IS UNIQUE" in s
        for s in statements
    )


def test_composite_identity_still_uses_a_tuple_constraint(ontology):
    """seocho-uxs: a per-member UNIQUE would reject two distinct entities that
    share one member. The tuple is the identity."""
    statements = ontology.to_cypher_constraints()
    assert any(
        "REQUIRE (n.name, n.procedure) IS UNIQUE" in s for s in statements
    )


def test_fulltext_index_exists_and_uses_neo4j_5_label_syntax(ontology):
    """Entity anchoring is a fuzzy substring match, which a RANGE index cannot
    serve. `graph_router.py` already queries `entity_fulltext` and nothing in
    the SDK created it, so that retrieval path failed and was swallowed by a
    bare except — degrading to a keyword heuristic with no signal."""
    statements = ontology.to_cypher_constraints()
    fulltext = [s for s in statements if "FULLTEXT INDEX" in s]
    assert fulltext, "no fulltext index; entity anchoring cannot use one"

    statement = fulltext[0]
    assert "entity_fulltext" in statement, "must match the name graph_router queries"
    # Neo4j 5 separates labels with `|`. A comma is a syntax error.
    assert "|" in statement and "n:Decision, " not in statement


def test_no_index_for_an_empty_ontology():
    """A fulltext index over no labels is a syntax error, not an empty index."""
    assert Ontology(name="empty", nodes={}).to_cypher_constraints() == []


def test_declared_index_and_unique_still_emitted():
    """The new coverage must not displace what was already correct."""
    onto = Ontology(
        name="t",
        nodes={"Co": NodeDef(
            description="c",
            properties={"ticker": P(str, index=True), "cik": P(str, unique=True)},
        )},
    )
    statements = onto.to_cypher_constraints()
    assert any("FOR (n:Co) ON (n.ticker)" in s for s in statements)
    assert any("REQUIRE n.cik IS UNIQUE" in s for s in statements)
