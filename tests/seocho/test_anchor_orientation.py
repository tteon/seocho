"""An anchor on the relationship's target end must flip the traversal, not the labels.

Found by running a query end to end against a live graph that contained the
answer, and getting "no data" back.

`_orient_relationship` detects that the model's (anchor_label, target_label)
pair contradicts the ontology's declared direction, and repaired it by swapping
the two labels. The anchor **entity** filter does not move with them, so the
generated query became:

    MATCH (a:Decision)-[:APPLIES_TO]-(b:System)
    WHERE toLower(a.name) CONTAINS toLower($anchor)     -- $anchor = "Payments API"

— a `Decision` named "Payments API", which does not exist. Measured: **0 rows**
on a graph holding both matching decisions, and the model then honestly reported
that it could not answer.

The premise was wrong. "Which decisions apply to the Payments API?" legitimately
anchors on the System; the ontology declares `APPLIES_TO: Decision -> System`, so
what has to run backwards is the **traversal**, not the labelling. That is what
`anchor_role` expresses, and every pattern builder already honours it.

After: 2 rows, carrying `status='applied'` and `status='superseded'` — the
property the question was actually about.
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.query.cypher_builder import CypherBuilder


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="incident",
        nodes={
            "System": NodeDef(description="s",
                              properties={"name": P(str, unique=True)},
                              identity_keys=["name"]),
            "Decision": NodeDef(description="d",
                                properties={"name": P(str, unique=True),
                                            "status": P(str)},
                                identity_keys=["name"]),
        },
        relationships={
            "APPLIES_TO": RelDef(source="Decision", target="System", description=""),
        },
    )


def _build(ontology, **kw):
    builder = CypherBuilder(ontology)
    cypher, params = builder.build(workspace_id="w", limit=20, **kw)
    return builder, cypher, params


def test_anchor_label_survives_the_repair(ontology):
    """The label the model gave is consistent with the entity it named."""
    builder, cypher, _ = _build(
        ontology, intent="relationship_lookup", anchor_entity="Payments API",
        anchor_label="System", target_label="Decision",
        relationship_type="APPLIES_TO",
    )
    assert "(a:`System`)" in cypher, (
        "the anchor variable lost the label of the entity bound to it, so the "
        "filter searches the wrong node type and matches nothing"
    )


def test_traversal_runs_backwards_instead(ontology):
    _, cypher, _ = _build(
        ontology, intent="relationship_lookup", anchor_entity="Payments API",
        anchor_label="System", target_label="Decision",
        relationship_type="APPLIES_TO",
    )
    assert "<-[r:`APPLIES_TO`]-" in cypher, (
        "the ontology declares Decision -> System, so an anchor on System must "
        "traverse the edge in reverse"
    )


def test_repair_is_recorded_for_measurement(ontology):
    builder, _, _ = _build(
        ontology, intent="relationship_lookup", anchor_entity="Payments API",
        anchor_label="System", target_label="Decision",
        relationship_type="APPLIES_TO",
    )
    repair = builder.last_orientation_repair
    assert repair is not None
    assert repair["reason"] == "anchor_on_relationship_target_end"
    assert repair["to"]["anchor_role"] == "target"


def test_anchor_on_the_source_end_is_left_alone(ontology):
    """The common case must not acquire a spurious repair."""
    builder, cypher, _ = _build(
        ontology, intent="relationship_lookup", anchor_entity="Retry Standard v2",
        anchor_label="Decision", target_label="System",
        relationship_type="APPLIES_TO",
    )
    assert "(a:`Decision`)" in cypher
    assert builder.last_orientation_repair is None
    assert "<-[" not in cypher, "a forward traversal was reversed"


def test_missing_anchor_label_is_filled_from_the_ontology(ontology):
    """The original case this guardrail was written for: the model names the
    relationship and the wrong end, with no target label to disambiguate."""
    _, cypher, _ = _build(
        ontology, intent="relationship_lookup", anchor_entity="Payments API",
        anchor_label="System", target_label="",
        relationship_type="APPLIES_TO",
    )
    assert "(a:`System`)" in cypher
    assert "(b:`Decision`)" in cypher, "the counterpart label was not derived"


def test_self_referential_relationship_is_not_reoriented():
    """A relationship whose ends share a label cannot be reversed."""
    onto = Ontology(
        name="t",
        nodes={"Decision": NodeDef(description="d",
                                   properties={"name": P(str, unique=True)},
                                   identity_keys=["name"])},
        relationships={"SUPERSEDES": RelDef(source="Decision", target="Decision",
                                            description="")},
    )
    builder, cypher, _ = _build(
        onto, intent="relationship_lookup", anchor_entity="Retry Standard v2",
        anchor_label="Decision", target_label="Decision",
        relationship_type="SUPERSEDES",
    )
    assert builder.last_orientation_repair is None


def test_role_does_not_leak_between_builds(ontology):
    """`build` may be called more than once on one builder (repair paths do)."""
    builder = CypherBuilder(ontology)
    builder.build(intent="relationship_lookup", anchor_entity="Payments API",
                  anchor_label="System", target_label="Decision",
                  relationship_type="APPLIES_TO", workspace_id="w", limit=20)
    _, second = builder.build(intent="relationship_lookup",
                              anchor_entity="Retry Standard v2",
                              anchor_label="Decision", target_label="System",
                              relationship_type="APPLIES_TO",
                              workspace_id="w", limit=20), None
    assert builder.anchor_role != "target", (
        "a derived role from an earlier build leaked into a later one"
    )
