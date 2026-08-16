"""An unnamed endpoint is an unknown to solve for, not a wildcard to match.

"Which retry standard is currently applied to the Payments API, and what did it
supersede?" names one endpoint and asks the system to find the other. The
planner extracted that correctly — `anchor='Payments API'` (System),
`target=''` (Decision), `relationship='SUPERSEDES'`. Three separate pieces of
machinery then mishandled the empty slot, and the end-to-end answer was
"I cannot answer this question" from a graph that contained the answer.

1. `_anchor_predicate` fell through to the text anchor, producing
   `toLower(...) CONTAINS toLower('')` — true for every node in the database.
   An accidental wildcard that reads as a filter, and one that forces a scan,
   since CONTAINS over a coalesce chain cannot use an index.

2. The relationship restriction was dropped entirely. `SUPERSEDES` is declared
   `Decision -> Decision` and cannot on its own reach a System anchor, so the
   pruning logic relaxed it to *everything*. Measured on a live graph: 20 paths,
   most of them `Chunk MENTIONS` detours through the provenance layer this
   pipeline writes itself.

3. `shortestPath` returns one path per endpoint pair, so a 1-hop `APPLIES_TO`
   edge hid every 2-hop `APPLIES_TO`/`SUPERSEDES` chain. The model said so:
   "the query results do not include any SUPERSEDES relationships".

Measured end to end after all three, same question and model:

    before: "Based on the query results, I cannot answer this question."
    after:  "Current retry standard: Retry Standard v2.
             What it superseded: Retry Standard v1."
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
            "Org": NodeDef(description="o", properties={"name": P(str, unique=True)},
                           identity_keys=["name"]),
            "System": NodeDef(description="s", properties={"name": P(str, unique=True)},
                              identity_keys=["name"]),
            "Decision": NodeDef(description="d",
                                properties={"name": P(str, unique=True), "status": P(str)},
                                identity_keys=["name"]),
        },
        relationships={
            "DECIDED": RelDef(source="Org", target="Decision", description=""),
            "APPLIES_TO": RelDef(source="Decision", target="System", description=""),
            "SUPERSEDES": RelDef(source="Decision", target="Decision", description=""),
        },
    )


def _unbound(ontology):
    builder = CypherBuilder(ontology)
    cypher, params = builder.build(
        intent="path", anchor_entity="Payments API", anchor_label="System",
        target_entity="", target_label="Decision", relationship_type="SUPERSEDES",
        workspace_id="w", limit=20,
    )
    return builder, cypher, params


# ---------------------------------------------------------------------------
# 1. No accidental wildcard
# ---------------------------------------------------------------------------

def test_empty_slot_does_not_become_a_contains_wildcard(ontology):
    _, cypher, params = _unbound(ontology)
    assert "$to_e" not in cypher, (
        "CONTAINS '' is true for every node — a wildcard that reads as a filter"
    )
    assert "to_e" not in params


def test_empty_slot_is_reported_as_unbound(ontology):
    """The caller must be able to tell 'no constraint' from 'constraint that
    happens to match everything'."""
    builder, _, _ = _unbound(ontology)
    assert builder.unbound_slots == ["b"]


def test_bound_slots_still_constrain(ontology):
    builder = CypherBuilder(ontology)
    cypher, params = builder.build(
        intent="path", anchor_entity="Payments API", anchor_label="System",
        target_entity="Retry Standard v2", target_label="Decision",
        relationship_type="APPLIES_TO", workspace_id="w", limit=20,
    )
    assert "$to_e" in cypher
    assert params["to_e"] == "Retry Standard v2"
    assert builder.unbound_slots == []


def test_unbound_slots_do_not_leak_between_builds(ontology):
    builder = CypherBuilder(ontology)
    builder.build(intent="path", anchor_entity="Payments API", anchor_label="System",
                  target_entity="", target_label="Decision",
                  relationship_type="SUPERSEDES", workspace_id="w", limit=20)
    builder.build(intent="path", anchor_entity="Payments API", anchor_label="System",
                  target_entity="Retry Standard v2", target_label="Decision",
                  relationship_type="APPLIES_TO", workspace_id="w", limit=20)
    assert builder.unbound_slots == []


# ---------------------------------------------------------------------------
# 2. Relaxation widens to what can connect, not to everything
# ---------------------------------------------------------------------------

def test_relaxation_keeps_a_relationship_restriction(ontology):
    _, cypher, _ = _unbound(ontology)
    assert "*..4]" in cypher
    assert "[:" in cypher, (
        "dropping the restriction admits every edge, including the Chunk/Section "
        "provenance layer this pipeline writes itself"
    )


def test_relaxation_includes_the_connecting_chain(ontology):
    builder, cypher, _ = _unbound(ontology)
    widened = builder.last_path_pruning["widened_to"]
    assert "APPLIES_TO" in widened, "the leg that reaches the anchor is missing"
    assert "SUPERSEDES" in widened, "the relationship the question named is missing"
    assert "APPLIES_TO" in cypher and "SUPERSEDES" in cypher


def test_connecting_types_exclude_unrelated_relationships(ontology):
    """DECIDED connects Org to Decision and is not on any System->Decision chain
    of interest; it must not be pulled in just because it touches Decision."""
    builder = CypherBuilder(ontology)
    connecting = builder._connecting_relationship_types("System", "Decision", max_hops=1)
    assert connecting == ["APPLIES_TO"]


def test_unsatisfiable_pattern_is_still_pruned(ontology):
    """The guardrail that prevents an exhaustive search for something the schema
    forbids must survive."""
    onto = Ontology(
        name="t",
        nodes={"A": NodeDef(description="a", properties={"name": P(str)}),
               "B": NodeDef(description="b", properties={"name": P(str)})},
        relationships={},
    )
    builder = CypherBuilder(onto)
    cypher, _ = builder.build(intent="path", anchor_entity="x", anchor_label="A",
                              target_entity="", target_label="B",
                              relationship_type="", workspace_id="w", limit=20)
    assert "LIMIT 0" in cypher


# ---------------------------------------------------------------------------
# 3. shortestPath is for bound pairs only
# ---------------------------------------------------------------------------

def test_unbound_endpoint_does_not_use_shortest_path(ontology):
    """One path per pair means a 1-hop edge hides every longer chain — which is
    exactly the chain an unbound question is asking to enumerate."""
    _, cypher, _ = _unbound(ontology)
    assert "shortestPath" not in cypher


def test_bound_pair_still_uses_shortest_path(ontology):
    """"How are these two connected" is what shortestPath is for, and
    enumerating all paths between two named nodes is expensive for no gain."""
    builder = CypherBuilder(ontology)
    cypher, _ = builder.build(
        intent="path", anchor_entity="Payments API", anchor_label="System",
        target_entity="Retry Standard v2", target_label="Decision",
        relationship_type="APPLIES_TO", workspace_id="w", limit=20,
    )
    assert "shortestPath" in cypher
