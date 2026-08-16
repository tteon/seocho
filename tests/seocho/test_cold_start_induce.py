"""Cold-start: upper ontology + post-pass ontology induction (design: cold-start-schema-bootstrap)."""

from __future__ import annotations

from seocho.ontology.upper import (
    UPPER_CATEGORIES,
    build_upper_ontology,
    render_upper_frame,
)
from seocho.ontology.induce import induce_ontology_from_graph, induction_report


def _anchored_graph():
    nodes = [
        {"id": "acme", "label": "Company", "properties": {"name": "Acme", "upper": "Organization"}},
        {"id": "globex", "label": "Company", "properties": {"name": "Globex", "upper": "Organization"}},
        {"id": "corp1", "label": "Corporation", "properties": {"name": "C1", "upper": "Organization"}},
        {"id": "sec", "label": "Regulator", "properties": {"name": "SEC", "upper": "Organization"}},
        {"id": "f1", "label": "Filing", "properties": {"name": "Q1", "upper": "Event"}},
        {"id": "f2", "label": "Filing", "properties": {"name": "Q2", "upper": "Event"}},
    ]
    rels = [
        {"source": "acme", "target": "f1", "type": "SUBMITTED"},
        {"source": "globex", "target": "f2", "type": "SUBMITTED"},
    ]
    return {"nodes": nodes, "relationships": rels}


def test_upper_ontology_shape():
    up = build_upper_ontology()
    assert "Organization" in up.nodes and up.nodes["Organization"].broader == ["Agent"]
    assert "Agent" in UPPER_CATEGORIES
    frame = render_upper_frame()
    assert "upper" in frame and "Organization" in frame and "specific type" in frame.lower()


def test_induce_types_anchored_to_upper():
    onto, axioms = induce_ontology_from_graph(_anchored_graph())
    # concrete types get broader = [upper anchor]
    assert onto.nodes["Company"].broader == ["Organization"]
    assert onto.nodes["Filing"].broader == ["Event"]
    # relationship endpoints induced from observed majority
    assert "SUBMITTED" in onto.relationships
    assert onto.relationships["SUBMITTED"].source == "Company"
    assert onto.relationships["SUBMITTED"].target == "Filing"


def test_induction_report_anchoring_and_grouping():
    rep = induction_report(_anchored_graph())
    assert rep["anchor_rate"] == 1.0
    # Company / Corporation / Regulator all cluster under Organization (drift axis)
    assert set(rep["types_per_upper"]["Organization"]) == {"Company", "Corporation", "Regulator"}
    assert rep["distinct_types"] == 4   # Company, Corporation, Regulator, Filing
