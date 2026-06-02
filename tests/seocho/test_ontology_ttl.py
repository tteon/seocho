"""Tests for the TTL I/O methods and ``+``/``-`` operators on Ontology."""

from pathlib import Path

import pytest

from seocho.ontology import NodeDef, Ontology, P, PropertyType, RelDef

pytest.importorskip("rdflib", reason="rdflib is in the seocho[ontology] extra")


@pytest.fixture
def base():
    return Ontology(
        name="base",
        namespace="https://seocho.dev/test/",
        nodes={
            "LegalEntity": NodeDef(
                description="A registered business",
                aliases=["Company", "Corporation"],
                properties={"name": P(type=PropertyType.STRING, unique=True)},
            ),
            "Person": NodeDef(
                description="An individual",
                properties={"name": P(type=PropertyType.STRING, unique=True)},
            ),
        },
        relationships={
            "EMPLOYS": RelDef(source="LegalEntity", target="Person", description="Employment"),
        },
    )


@pytest.fixture
def extension():
    return Ontology(
        name="extension",
        namespace="https://seocho.dev/test/",
        nodes={
            "Patent": NodeDef(properties={"name": P(type=PropertyType.STRING, unique=True)}),
            "Lawsuit": NodeDef(properties={"name": P(type=PropertyType.STRING, unique=True)}),
        },
        relationships={
            "OWNS": RelDef(source="LegalEntity", target="Patent"),
            "DEFENDANT_IN": RelDef(source="LegalEntity", target="Lawsuit"),
        },
    )


@pytest.fixture
def restricted():
    return Ontology(
        name="restricted",
        nodes={"Lawsuit": NodeDef(properties={"name": P(type=PropertyType.STRING, unique=True)})},
        relationships={"DEFENDANT_IN": RelDef(source="LegalEntity", target="Lawsuit")},
    )


def test_to_ttl_then_from_ttl_roundtrip(base, tmp_path: Path):
    out = base.to_ttl(tmp_path / "base.ttl")
    assert out.exists()
    text = out.read_text()
    assert "owl:Class" in text
    assert "owl:ObjectProperty" in text

    reloaded = Ontology.from_ttl(out)
    assert "LegalEntity" in reloaded.nodes
    assert "Person" in reloaded.nodes
    assert "EMPLOYS" in reloaded.relationships
    # Aliases preserved via skos:altLabel
    assert any("Company" in alias for alias in reloaded.nodes["LegalEntity"].aliases)


def test_subtract_method_drops_labels_and_dependent_rels(base, extension, restricted):
    composed = base.merge(extension)
    assert "Lawsuit" in composed.nodes
    assert "DEFENDANT_IN" in composed.relationships

    pruned = composed.subtract(restricted)
    assert "Lawsuit" not in pruned.nodes
    assert "DEFENDANT_IN" not in pruned.relationships
    # Unrelated content stays
    assert "LegalEntity" in pruned.nodes
    assert "EMPLOYS" in pruned.relationships
    assert "Patent" in pruned.nodes


def test_add_and_sub_operators_compose(base, extension, restricted):
    composed = base + extension - restricted
    assert isinstance(composed, Ontology)
    assert {"LegalEntity", "Person", "Patent"}.issubset(composed.nodes.keys())
    assert "Lawsuit" not in composed.nodes
    assert "DEFENDANT_IN" not in composed.relationships


def test_subtract_drops_relationships_pointing_at_removed_node(extension, base):
    # Relationships whose source or target was removed should disappear,
    # even if the relationship type itself isn't named in `right`.
    target_only = Ontology(
        name="drop_lawsuit_only",
        nodes={"Lawsuit": NodeDef(properties={})},
        relationships={},
    )
    composed = (base + extension) - target_only
    assert "Lawsuit" not in composed.nodes
    assert "DEFENDANT_IN" not in composed.relationships
    assert "OWNS" in composed.relationships  # still references LegalEntity -> Patent
