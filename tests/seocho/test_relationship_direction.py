"""Direction survives the projection for same-label relationships (seocho-k5n).

`_relationship_lookup` matched undirected and then named the *anchor* `source`
and the neighbour `target`, discarding the arrow. When both endpoints carry the
same label — every payments, ownership or transfer schema — that inverts each
incoming edge. Asked "which accounts transferred to B2" against a graph holding
`A1 -TRANSFER-> B2`, it returned `source=B2, target=A1` and the model answered
the exact opposite of the graph, confidently, with no error raised.

The ontology already declared the fix: `TRANSFER` carries
`sourceRole: sender` / `targetRole: beneficiary`, and the planner infers
`anchor_role` correctly. Two of five templates honoured it; this one did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.ontology import Ontology  # noqa: E402
from seocho.query.cypher_builder import CypherBuilder  # noqa: E402

_SAME_LABEL_ONTOLOGY = {
    "name": "directiontest",
    "nodes": {
        "Account": {"properties": {"name": "string"}},
        "Institution": {"properties": {"name": "string"}},
    },
    "relationships": {
        # Both endpoints share a label, so the arrow — not the label — carries
        # the direction. This is the shape the bug was invisible outside of.
        "TRANSFER": {
            "source": "Account",
            "target": "Account",
            "source_role": "sender",
            "target_role": "beneficiary",
        },
        "HELD_AT": {"source": "Account", "target": "Institution"},
    },
}


def _build(role: str) -> str:
    """Build the relationship_lookup Cypher with the declared anchor role.

    ``anchor_role`` reaches the builder through ``schema_hints`` — that is the
    channel the planner fills from the intent payload.
    """
    builder = CypherBuilder(Ontology.from_dict(_SAME_LABEL_ONTOLOGY))
    cypher, _params = builder.build(
        intent="relationship_lookup",
        anchor_entity="B2",
        anchor_label="Account",
        relationship_type="TRANSFER",
        workspace_id="default",
        schema_hints={"anchor_role": role} if role else {},
    )
    return cypher


@pytest.mark.parametrize("role", ["", "source", "target"])
def test_source_and_target_are_read_off_the_edge(role):
    """Never label the anchor `source`; the arrow decides, at every role."""
    cypher = _build(role)
    assert "startNode(r)" in cypher, "source must come from the edge, not binding order"
    assert "endNode(r)" in cypher, "target must come from the edge, not binding order"
    assert "coalesce(a.name, a.uri) AS source" not in cypher
    assert "coalesce(b.name, b.uri) AS target" not in cypher


def test_declared_anchor_role_constrains_the_pattern():
    """A question that names a direction must not match the other one."""
    incoming = _build("target")
    outgoing = _build("source")

    # anchor is the beneficiary -> edges point INTO it
    assert "<-[r" in incoming
    assert "]->(b" not in incoming
    # anchor is the sender -> edges point OUT of it
    assert "]->(b" in outgoing
    assert "<-[r" not in outgoing


def test_unknown_role_stays_undirected():
    """With no declared role, recall must not silently halve."""
    cypher = _build("")
    assert "<-[r" not in cypher
    assert "]->(b" not in cypher
    assert "-[r" in cypher


def test_neighbour_columns_still_describe_the_counterpart():
    """The direction fix must not move what the answer synthesiser reads."""
    cypher = _build("target")
    assert "labels(b) AS target_labels" in cypher
    assert "properties(b) AS target_properties" in cypher
    assert "AS supporting_fact" in cypher
