"""Fold an over-specified relationship type onto a declared generic one.

The exact relationship-side symmetry of the label coercion in #583. A generic
ontology declares one relationship, `RELATED_TO`, whose description says to put
the verb in a property; a model with no grammar enforcement instead emits the
verb AS the type (`IS_ALSO_KNOWN_AS`, `GROWS_IN`). Where the canonical-vocabulary
check rejects an unknown type, that drops the edge.

Honest scope note: this is a *symmetric hardening*, tested at the unit level. In
live runs where the model already emits `RELATED_TO`, it is a no-op — so it is
not claimed to move a benchmark recall number on its own. It closes the case
where the model over-specifies the type, the same way #583 closed the label case,
and preserves the specificity in `verb` rather than discarding the edge.
"""

from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.pipeline import IndexingPipeline


def _generic() -> Ontology:
    return Ontology(
        name="generic",
        nodes={"Entity": NodeDef(properties={"name": P(str, unique=True), "kind": P(str)},
                                 identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity",
                                            description="A stated relationship.")},
    )


def _multi() -> Ontology:
    return Ontology(
        name="multi",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)}),
               "Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company", description=""),
            "FOUNDED": RelDef(source="Person", target="Company", description=""),
        },
    )


def _pipe(onto):
    return IndexingPipeline(ontology=onto, graph_store=object(), llm=object())


def test_off_type_is_folded_onto_the_sole_declared_relationship():
    rels = [{"source": "e1", "target": "e2", "type": "IS_ALSO_KNOWN_AS",
             "properties": {}}]
    out = _pipe(_generic())._coerce_generic_relationship_types(rels)
    assert out[0]["type"] == "RELATED_TO"
    assert out[0]["properties"]["verb"] == "IS_ALSO_KNOWN_AS", (
        "the specific relation must be preserved, not discarded"
    )


def test_matching_type_is_untouched():
    rels = [{"source": "e1", "target": "e2", "type": "RELATED_TO", "properties": {}}]
    out = _pipe(_generic())._coerce_generic_relationship_types(rels)
    assert out[0]["type"] == "RELATED_TO"
    assert "verb" not in out[0]["properties"], "a no-op must not invent a verb"


def test_a_verb_the_model_set_is_not_overwritten():
    rels = [{"source": "e1", "target": "e2", "type": "GROWS_IN",
             "properties": {"verb": "is native to"}}]
    out = _pipe(_generic())._coerce_generic_relationship_types(rels)
    assert out[0]["properties"]["verb"] == "is native to"


def test_multi_relationship_ontology_is_a_no_op():
    """Coercion needs exactly one declared relationship to fold onto. A
    multi-relationship ontology keeps its existing mapping/rejection path."""
    rels = [{"source": "e1", "target": "e2", "type": "EMPLOYED_BY",
             "properties": {}}]
    out = _pipe(_multi())._coerce_generic_relationship_types(rels)
    assert out[0]["type"] == "EMPLOYED_BY", "an off-type must not be coerced"


def test_pipeline_calls_relationship_coercion():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "index" / "pipeline.py").read_text()
    assert "_coerce_generic_relationship_types(rels)" in src, (
        "the coercion is defined but never called"
    )
