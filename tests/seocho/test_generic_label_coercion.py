"""An over-specified label folds onto a declared generic class.

A generic open-domain ontology declares one catch-all class -- `Entity` with a
`kind` property whose description enumerates person/place/organisation/etc --
precisely so the specific type lives in `kind`, not in the label. A hosted model
with no grammar enforcement reads that enumeration and promotes it: it emits
`label="Place"`, `label="Person"`, which the query side (matching `(:Entity)`)
cannot find.

Measured over ten benchmark documents on a live MARA MiniMax-M2.7 run, this was
the dominant failure: 8 of 10 questions returned no result against graphs full
of correctly-extracted facts sitting under the wrong labels
(`Place:34, Organisation:4, Person:3, ...` with `Entity:0`).

Two prior guards did not catch it:
  - the extraction prompt already lists `Entity` as the only type, and the
    model ignored it (no grammar on a hosted endpoint);
  - `off_ontology_label` counted the deviation but, under the default guided
    enforcement, the nodes were still written.

So the pipeline folds the emitted label back onto `Entity` and preserves it as
`kind`. The specificity is not lost; it moves to where the ontology said it
belongs.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.pipeline import IndexingPipeline


def _generic() -> Ontology:
    return Ontology(
        name="generic",
        nodes={"Entity": NodeDef(
            description="A named thing: person, place, organisation, concept.",
            properties={"name": P(str, unique=True), "kind": P(str)},
            identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity",
                                            description="A stated relationship.")},
    )


def _rich() -> Ontology:
    return Ontology(
        name="rich",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={},
    )


def _pipe(onto):
    # graph_store/llm are unused by _coerce_generic_labels.
    return IndexingPipeline(ontology=onto, graph_store=object(), llm=object())


def test_off_label_is_folded_onto_entity():
    pipe = _pipe(_generic())
    nodes = [{"id": "n1", "label": "Place",
              "properties": {"name": "Cornwall"}}]
    out = pipe._coerce_generic_labels(nodes)
    assert out[0]["label"] == "Entity"
    assert out[0]["properties"]["kind"] == "Place", (
        "the emitted specificity must be preserved in kind, not discarded"
    )


def test_declared_label_is_untouched():
    pipe = _pipe(_generic())
    nodes = [{"id": "n1", "label": "Entity",
              "properties": {"name": "x", "kind": "plant"}}]
    out = pipe._coerce_generic_labels(nodes)
    assert out[0]["label"] == "Entity"
    assert out[0]["properties"]["kind"] == "plant", "an existing kind is kept"


def test_a_kind_the_model_set_is_not_overwritten():
    pipe = _pipe(_generic())
    nodes = [{"id": "n1", "label": "Place",
              "properties": {"name": "Cornwall", "kind": "county"}}]
    out = pipe._coerce_generic_labels(nodes)
    assert out[0]["properties"]["kind"] == "county", (
        "coercion must not clobber a kind the model already provided"
    )


def test_provenance_labels_are_not_coerced():
    pipe = _pipe(_generic())
    nodes = [{"id": "c1", "label": "Chunk", "properties": {}}]
    out = pipe._coerce_generic_labels(nodes)
    assert out[0]["label"] == "Chunk", "the provenance layer is not the model's"


def test_rich_ontology_is_a_no_op():
    """No catch-all to coerce onto: off-ontology labels fall through to the
    existing validation, which warns or rejects per enforcement mode."""
    pipe = _pipe(_rich())
    nodes = [{"id": "n1", "label": "Place", "properties": {"name": "x"}}]
    out = pipe._coerce_generic_labels(nodes)
    assert out[0]["label"] == "Place", (
        "a rich ontology must keep its off-label handling; coercion needs a "
        "declared Entity class and this one has none"
    )


def test_declared_labels_in_a_mixed_ontology_survive():
    """If Entity is one of several declared classes, the others still pass."""
    onto = Ontology(
        name="mixed",
        nodes={
            "Entity": NodeDef(properties={"name": P(str, unique=True), "kind": P(str)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={},
    )
    out = _pipe(onto)._coerce_generic_labels([
        {"id": "1", "label": "Company", "properties": {"name": "Acme"}},
        {"id": "2", "label": "Place", "properties": {"name": "Cornwall"}},
    ])
    labels = {n["id"]: n["label"] for n in out}
    assert labels["1"] == "Company", "a declared label must not be coerced"
    assert labels["2"] == "Entity", "an undeclared label folds onto Entity"


def test_the_prompt_stops_forbidding_a_declared_entity():
    """The other half of the fix: strict guidance no longer tells a generic
    ontology never to use its own only label."""
    from seocho.index.extraction_engine import CanonicalExtractionEngine

    class _LLM:
        def __init__(self):
            self.calls = []

        def complete(self, *, system, user, **kw):
            self.calls.append(system)

            class _R:
                text = '{"nodes": [], "relationships": []}'

                def json(self):
                    return {"nodes": [], "relationships": []}
            return _R()

    llm = _LLM()
    engine = CanonicalExtractionEngine(ontology=_generic(), llm=llm,
                                       enforcement="strict")
    engine.extract("Some text.")
    assert "Do not fall back to a generic 'Entity'" not in llm.calls[0]
