"""Subclass (broader) <-> rdfs:subClassOf round-trip (gap-closure plan item #3).

from_ttl must capture rdfs:subClassOf into NodeDef.broader; to_ttl must emit it;
the pair must round-trip. Requires rdflib (seocho[ontology] extra) — skipped if
absent.
"""
from __future__ import annotations

import pytest

rdflib = pytest.importorskip("rdflib")

from seocho import NodeDef, Ontology, P, RelDef


def test_to_ttl_emits_and_from_ttl_recovers_subclassof(tmp_path):
    onto = Ontology(
        name="fin",
        nodes={
            "FinancialMetric": NodeDef(description="base", properties={"name": P(str, unique=True)}),
            "Revenue": NodeDef(description="top-line", broader=["FinancialMetric"],
                               properties={"name": P(str, unique=True)}),
            "NetIncome": NodeDef(description="bottom-line", broader=["FinancialMetric"],
                                 properties={"name": P(str, unique=True)}),
        },
        relationships={},
    )
    ttl = tmp_path / "fin.ttl"
    onto.to_ttl(ttl)
    text = ttl.read_text()
    assert "subClassOf" in text  # broader emitted as rdfs:subClassOf

    loaded = Ontology.from_ttl(ttl)
    assert loaded.nodes["Revenue"].broader == ["FinancialMetric"]
    assert loaded.nodes["NetIncome"].broader == ["FinancialMetric"]
    assert loaded.nodes["FinancialMetric"].broader == []  # base has no parent


def test_from_ttl_reads_handwritten_subclassof(tmp_path):
    ttl = tmp_path / "h.ttl"
    ttl.write_text(
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex: <http://example.org/> .

ex:FinancialMetric a owl:Class ; rdfs:label "Financial metric" .
ex:Revenue a owl:Class ; skos:definition "Top-line revenue" ;
    rdfs:subClassOf ex:FinancialMetric .
"""
    )
    onto = Ontology.from_ttl(ttl)
    assert onto.nodes["Revenue"].broader == ["FinancialMetric"]
    # skos:definition preferred for description
    assert onto.nodes["Revenue"].description == "Top-line revenue"


def test_validate_flags_dangling_and_circular_broader():
    dangling = Ontology(
        name="x",
        nodes={"R": NodeDef(description="d", broader=["Nope"],
                            properties={"name": P(str, unique=True)})},
        relationships={},
    )
    assert any("broader references unknown" in e for e in dangling.validate())

    cyclic = Ontology(
        name="x",
        nodes={"A": NodeDef(description="d", broader=["B"], properties={"name": P(str, unique=True)}),
               "B": NodeDef(description="d", broader=["A"], properties={"name": P(str, unique=True)})},
        relationships={},
    )
    assert any("circular broader" in e for e in cyclic.validate())

    clean = Ontology(
        name="x",
        nodes={"FinancialMetric": NodeDef(description="d", properties={"name": P(str, unique=True)}),
               "Revenue": NodeDef(description="d", broader=["FinancialMetric"],
                                  properties={"name": P(str, unique=True)})},
        relationships={},
    )
    assert clean.validate() == []


def test_external_or_blank_supers_are_dropped(tmp_path):
    ttl = tmp_path / "x.ttl"
    ttl.write_text(
        """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.org/> .
ex:Revenue a owl:Class ; rdfs:subClassOf <http://external.org/NotInOntology> .
"""
    )
    onto = Ontology.from_ttl(ttl)
    # external super isn't a defined class here -> not added to broader
    assert onto.nodes["Revenue"].broader == []
