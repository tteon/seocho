"""Tests for the DataHub approval round-trip (ADR-0129 follow-up)."""

from __future__ import annotations

import pytest

from seocho.datahub_export import (
    _term_urn,
    datahub_glossary_to_mapping_spec,
    ontology_to_glossary_mcps,
)
from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import apply_mapping_spec


def _terms():
    return [
        {"name": "Regulation", "review_status": "APPROVED", "action": "new_class",
         "parent": "Concept", "description": "A rule."},
        {"name": "Adj. EBITDA", "review_status": "APPROVED", "action": "alias", "target": "FinancialMetric"},
        {"name": "Maybe", "review_status": "PROPOSED", "action": "new_class", "parent": "Concept"},  # not approved
        {"name": "junk", "status": "REJECTED", "action": "ignore"},
    ]


def test_only_approved_terms_become_mappings():
    spec = datahub_glossary_to_mapping_spec(_terms(), only_status="APPROVED")
    surfaces = {m["surface"]: m for m in spec["mappings"]}
    assert set(surfaces) == {"Regulation", "Adj. EBITDA"}   # PROPOSED + REJECTED excluded
    assert surfaces["Regulation"]["action"] == "new_class"
    assert surfaces["Regulation"]["parent"] == "Concept"
    assert surfaces["Adj. EBITDA"]["action"] == "alias"
    assert surfaces["Adj. EBITDA"]["target"] == "FinancialMetric"


def test_roundtrip_applies_to_ontology():
    onto = Ontology("biz", version="1.0.0", nodes={
        "Concept": NodeDef(description="A concept."),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
    })
    spec = datahub_glossary_to_mapping_spec(_terms(), only_status="APPROVED", ontology_name=onto.name)
    new_onto = apply_mapping_spec(onto, spec)
    assert "Regulation" in new_onto.nodes
    assert new_onto.nodes["Regulation"].broader == ["Concept"]
    assert "Adj. EBITDA" in new_onto.nodes["FinancialMetric"].aliases
    assert new_onto.version == "1.1.0"  # minor bump


def test_empty_when_no_approved():
    spec = datahub_glossary_to_mapping_spec(
        [{"name": "x", "review_status": "PROPOSED", "action": "new_class"}], only_status="APPROVED")
    assert spec["mappings"] == []


# --- annotate action (seocho-v6w.9): human edits an existing term's definition -


def test_annotate_carries_description_edit_back():
    spec = datahub_glossary_to_mapping_spec(
        [{"name": "Animal", "review_status": "APPROVED", "action": "annotate",
          "description": "A living creature in the corpus."}],
        only_status="APPROVED")
    assert spec["mappings"] == [
        {"surface": "Animal", "action": "annotate", "target": "Animal",
         "description": "A living creature in the corpus."}]


def test_annotate_updates_existing_class_description():
    onto = Ontology("biz", version="1.0.0", nodes={
        "Animal": NodeDef(description=""),  # blank; reviewer fills it in DataHub
    })
    spec = datahub_glossary_to_mapping_spec(
        [{"name": "Animal", "review_status": "APPROVED", "action": "annotate",
          "description": "A living creature in the corpus."}], only_status="APPROVED")
    new_onto = apply_mapping_spec(onto, spec)
    assert new_onto.nodes["Animal"].description == "A living creature in the corpus."
    assert len(new_onto.nodes) == 1  # annotate never creates
    assert new_onto.version == "1.1.0"


def test_annotate_can_add_alias_to_existing_class():
    onto = Ontology("biz", version="1.0.0", nodes={
        "Animal": NodeDef(description="A creature.", properties={"name": P(str, unique=True)}),
    })
    new_onto = apply_mapping_spec(onto, {"mappings": [
        {"surface": "Animal", "action": "annotate", "target": "Animal", "alias": "critter"}]})
    assert "critter" in new_onto.nodes["Animal"].aliases


def test_annotate_missing_class_raises():
    onto = Ontology("biz", version="1.0.0", nodes={"Animal": NodeDef(description="x")})
    with pytest.raises(ValueError, match="annotate target class not found"):
        apply_mapping_spec(onto, {"mappings": [
            {"surface": "Ghost", "action": "annotate", "target": "Ghost", "description": "y"}]})


def test_annotate_empty_description_does_not_clear():
    onto = Ontology("biz", version="1.0.0", nodes={"Animal": NodeDef(description="keep me")})
    new_onto = apply_mapping_spec(onto, {"mappings": [
        {"surface": "Animal", "action": "annotate", "target": "Animal", "description": ""}]})
    assert new_onto.nodes["Animal"].description == "keep me"


# --- outbound clobber-prevention: preserve_definitions on re-export -----------


def test_preserve_definitions_skips_term_info_but_keeps_taxonomy():
    onto = Ontology("biz", version="1.0.0", nodes={
        "Animal": NodeDef(description="human-owned text", properties={"name": P(str, unique=True)}),
        "Breed": NodeDef(description="A breed.", properties={"name": P(str, unique=True)}, broader=["Animal"]),
    })
    mcps = ontology_to_glossary_mcps(onto, preserve_definitions=["Animal"])
    animal_urn = _term_urn(f"{onto.package_id or onto.name}.Animal")
    # no glossaryTermInfo asserted for the preserved term (definition not clobbered)
    assert not any(m["entityUrn"] == animal_urn and m["aspectName"] == "glossaryTermInfo"
                   for m in mcps)
    # but the non-preserved term still gets its info, and taxonomy is still emitted
    breed_urn = _term_urn(f"{onto.package_id or onto.name}.Breed")
    assert any(m["entityUrn"] == breed_urn and m["aspectName"] == "glossaryTermInfo" for m in mcps)
    assert any(m["aspectName"] == "glossaryRelatedTerms" for m in mcps)
