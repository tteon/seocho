"""Project what the ontology declares, not the whole node.

`properties(n)` returns everything the writer stored, and most of it is this
pipeline's own bookkeeping. Measured against a live graph on a two-row answer,
23 keys came back:

    _ontology_artifact_hash  _ontology_context_hash  _ontology_glossary_hash
    _ontology_graph_model    _ontology_id            _ontology_name
    _ontology_profile        _ontology_schema_fingerprint
    _ontology_version        _ontology_version_valid _source_id  _sources
    _workspace_id            _writer_agent           _writer_ts
    category  id  memory_id  name  source_id  source_type  status
    updated_at  workspace_id

The ontology declared **two** of them. 2213 characters of answer context, of
which 435 carried information: **80% provenance**.

Cost is the smaller half. The larger half is that a model handed twenty internal
keys alongside two meaningful ones has to guess which carry the answer, and
`status` — the property the question was about — arrives with exactly the same
weight as `_ontology_glossary_hash`.

The path template had the opposite defect: it projected node *names* and
relationship types and no properties at all, so a question about `status` could
not be answered from its rows however correct the traversal was.
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef

from seocho.query.cypher_builder import CypherBuilder

INTERNAL = (
    "_workspace_id", "_sources", "_source_id", "_writer_agent", "_writer_ts",
    "_ontology_version", "_ontology_schema_fingerprint",
)



def _return_clause(cypher: str) -> str:
    """Only the projection. `_workspace_id` legitimately appears in the WHERE —
    it is the tenancy filter — so scanning the whole query is a false positive."""
    index = cypher.upper().rindex("RETURN")
    return cypher[index:]


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


def _lookup(ontology):
    return CypherBuilder(ontology).build(
        intent="relationship_lookup", anchor_entity="Payments API",
        anchor_label="System", target_label="Decision",
        relationship_type="APPLIES_TO", workspace_id="w", limit=20,
    )[0]


# ---------------------------------------------------------------------------
# relationship_lookup
# ---------------------------------------------------------------------------

def test_declared_properties_are_projected(ontology):
    cypher = _lookup(ontology)
    assert "`status`: b.`status`" in cypher, (
        "the property the question is about must reach the answer context"
    )
    assert "`name`: b.`name`" in cypher


def test_whole_node_is_not_projected(ontology):
    cypher = _lookup(ontology)
    assert "properties(b)" not in cypher, (
        "properties() returns the pipeline's own _ontology_*/_workspace_id "
        "bookkeeping on every row"
    )


@pytest.mark.parametrize("internal", INTERNAL)
def test_internal_properties_are_not_named(ontology, internal):
    assert internal not in _return_clause(_lookup(ontology))


def test_unknown_label_still_returns_something(ontology):
    """Returning nothing is worse than returning too much."""
    cypher = CypherBuilder(ontology).build(
        intent="relationship_lookup", anchor_entity="x",
        anchor_label="System", target_label="",
        relationship_type="APPLIES_TO", workspace_id="w", limit=20,
    )[0]
    assert "target_properties" in cypher


def test_projection_uses_no_procedure_call(ontology):
    """The read path refuses procedure calls (enforce_read_workspace_scope), so
    a projection must not need apoc."""
    cypher = _lookup(ontology)
    assert "apoc." not in cypher
    assert "CALL" not in cypher.upper()


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------

def _path(ontology, **kw):
    params = dict(intent="path", anchor_entity="Payments API",
                  anchor_label="System", target_entity="",
                  target_label="Decision", relationship_type="APPLIES_TO",
                  workspace_id="w", limit=20)
    params.update(kw)
    return CypherBuilder(ontology).build(**params)[0]


def test_path_projects_properties(ontology):
    """A correct traversal whose rows carry only names cannot answer a question
    about a property."""
    cypher = _path(ontology)
    assert "node_properties" in cypher
    assert ".`status`" in cypher


def test_path_projection_is_bounded_by_the_ontology(ontology):
    cypher = _path(ontology)
    assert "properties(n)" not in cypher
    projection = _return_clause(cypher)
    for internal in INTERNAL:
        assert internal not in projection


def test_path_projection_is_capped():
    """A wide ontology must not put an unbounded key list on every path node."""
    onto = Ontology(
        name="wide",
        nodes={"Thing": NodeDef(
            description="t",
            properties={f"p{i}": P(str) for i in range(40)} | {"name": P(str, unique=True)},
            identity_keys=["name"],
        )},
        relationships={},
    )
    expr = CypherBuilder(onto)._path_props_expr("n")
    assert expr.count(".") <= 13, "projection grew with the ontology without bound"


def test_identity_keys_survive_the_cap():
    """If the cap binds, keep the keys that identify a node over the ones that
    merely describe it."""
    onto = Ontology(
        name="wide",
        nodes={"Thing": NodeDef(
            description="t",
            properties={f"p{i}": P(str) for i in range(40)} | {"code": P(str, unique=True)},
            identity_keys=["code"],
        )},
        relationships={},
    )
    assert ".`code`" in CypherBuilder(onto)._path_props_expr("n")


def test_empty_ontology_falls_back_to_a_display_expression():
    onto = Ontology(name="empty", nodes={}, relationships={})
    expr = CypherBuilder(onto)._path_props_expr("n")
    assert expr and "coalesce" in expr
