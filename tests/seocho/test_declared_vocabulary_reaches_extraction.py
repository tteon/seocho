"""A declared vocabulary must reach the extractor, and the counter must see it.

Found by running the pipeline end to end against a live DozerDB and MiniMax-M2.7,
not by reading code. Two independent gaps, both silent:

**The enum never reached the prompt.** `P(str, enum=[...])` was emitted as
`sh:in` and enforced by `validate_with_shacl`, and rendered nowhere. So the
model was rejected after the fact for a rule it was never told. ADR-0181's own
finding was that what has a declarative home in the prompt is obeyed
(`Step.position`, 175/175) and what does not is invented (`Decision.status`,
eight values across 51 documents) — so rendering is the half that does the work.

**The counter read the wrong declaration site.** `record_off_vocabulary` was
wired to `annotations["vocabularies"]` (the OS-contract sidecar) only, so an
ontology declaring `P(str, enum=[...])` had a vocabulary the metric could not
see. It reported zero deviations for a property that was deviating on every
node.

Measured, same document and same model, before and after:

    status='active'      'active'        <- enum declared, not rendered
    status='superseded'  'applied'       <- enum rendered
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, P


VOCAB = ["proposed", "applied", "superseded", "reverted"]


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="incident",
        nodes={
            "Decision": NodeDef(
                description="A decision taken",
                properties={
                    "name": P(str, unique=True),
                    "status": P(str, enum=VOCAB),
                    "confidence": P(float, value_range=(0.0, 1.0)),
                },
                identity_keys=["name"],
            ),
        },
    )


def test_enum_is_rendered_into_the_extraction_context(ontology):
    """The model cannot honour a constraint it is never shown."""
    rendered = " ".join(ontology.to_extraction_context().values())
    assert "Decision.status" in rendered
    for value in VOCAB:
        assert value in rendered, f"{value} never reaches the extractor"


def test_range_is_rendered_too(ontology):
    rendered = " ".join(ontology.to_extraction_context().values())
    assert "Decision.confidence" in rendered
    assert "0.0" in rendered and "1.0" in rendered


def test_plain_property_is_not_given_a_vocabulary(ontology):
    """Only declared constraints render; a bare P(str) must stay unconstrained."""
    rendered = " ".join(ontology.to_extraction_context().values())
    assert "Decision.name: MUST be exactly one of" not in rendered


def test_pipeline_derives_vocabularies_from_property_enum():
    """The counter must read the SDK-native declaration site, not only the
    sidecar. Reading one of two homes made the metric report zero deviations
    for a property deviating on every node."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "src" / "seocho" / "index" / "pipeline.py").read_text()
    assert 'getattr(_prop, "enum", None)' in source, (
        "P(enum=...) is invisible to the off-vocabulary counter"
    )
    assert '"vocabularies"' in source, "the sidecar site must still be read"


def test_both_declaration_sites_compose():
    """Sidecar and P(enum=) must merge, not replace one another."""
    onto = Ontology(
        name="t",
        nodes={
            "Decision": NodeDef(description="d",
                                properties={"status": P(str, enum=VOCAB)}),
            "Step": NodeDef(description="s", properties={"phase": P(str)}),
        },
    )
    onto.annotations = {"vocabularies": {"Step.phase": ["start", "end"]}}

    merged = dict(onto.annotations.get("vocabularies") or {})
    for label, node in onto.nodes.items():
        for name, prop in node.properties.items():
            if getattr(prop, "enum", None):
                merged.setdefault(f"{label}.{name}", list(prop.enum))

    assert merged == {
        "Step.phase": ["start", "end"],
        "Decision.status": VOCAB,
    }


def test_provenance_layer_is_not_an_ontology_violation():
    """The pipeline writes Document / DocumentVersion / Section / Chunk itself.
    Counting those as off-ontology reported a constant 4 on every document
    forever — a metric always wrong by the same amount still moves correctly,
    so nobody questions the baseline."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "src" / "seocho" / "index" / "pipeline.py").read_text()
    assert "_system_layer_label(n.get(\"label\"))" in source, (
        "the provenance layer is still counted against the ontology"
    )
