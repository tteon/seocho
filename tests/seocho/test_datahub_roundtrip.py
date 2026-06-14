"""Tests for the DataHub approval round-trip (ADR-0129 follow-up)."""

from __future__ import annotations

from seocho.datahub_export import datahub_glossary_to_mapping_spec
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
