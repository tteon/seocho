"""Tests for the DataHub glossary connector (PoC, seocho-qxj)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.datahub_export import (
    emit_to_datahub,
    export_summary,
    ontology_to_glossary_mcps,
)


def _onto() -> Ontology:
    return Ontology("people-orgs", package_id="po", version="1.2.0", description="People and orgs.", nodes={
        "Agent": NodeDef(description="Anything that acts."),
        "Person": NodeDef(description="A human.", broader=["Agent"], aliases=["Individual"],
                          properties={"name": P(str, unique=True)}, same_as="schema:Person"),
        "Company": NodeDef(description="A firm.", broader=["Agent"], properties={"name": P(str, unique=True)}),
    }, relationships={
        "WORKS_AT": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE", description="employment"),
    })


def test_mcps_have_package_node_and_terms():
    mcps = ontology_to_glossary_mcps(_onto())
    s = export_summary(mcps)
    assert s["glossary_terms"] == 3 + 1  # 3 classes + 1 relationship term
    assert s["glossary_nodes"] == 2      # package node + Relationships node
    assert s["is_a_edges"] == 2          # Person->Agent, Company->Agent


def test_term_urns_deterministic_and_idempotent():
    a = ontology_to_glossary_mcps(_onto())
    b = ontology_to_glossary_mcps(_onto())
    assert a == b  # deterministic → idempotent UPSERT
    person = next(m for m in a if m["aspectName"] == "glossaryTermInfo" and m["aspect"]["name"] == "Person")
    assert person["entityUrn"] == "urn:li:glossaryTerm:po.Person"
    assert person["aspect"]["parentNode"] == "urn:li:glossaryNode:po"
    assert person["changeType"] == "UPSERT"


def test_custom_properties_carry_seocho_metadata():
    mcps = ontology_to_glossary_mcps(_onto())
    person = next(m for m in mcps if m["aspectName"] == "glossaryTermInfo" and m["aspect"]["name"] == "Person")
    cp = person["aspect"]["customProperties"]
    assert cp["aliases"] == "Individual"
    assert cp["same_as"] == "schema:Person"
    assert cp["identity_keys"] == "name"
    assert cp["ontology_version"] == "1.2.0"


def test_is_a_edge_aspect():
    mcps = ontology_to_glossary_mcps(_onto())
    rel = [m for m in mcps if m["aspectName"] == "glossaryRelatedTerms"]
    person_rel = next(m for m in rel if m["entityUrn"] == "urn:li:glossaryTerm:po.Person")
    assert person_rel["aspect"]["isRelatedTerms"] == ["urn:li:glossaryTerm:po.Agent"]


def test_relationship_term_has_endpoints():
    mcps = ontology_to_glossary_mcps(_onto())
    rel_term = next(m for m in mcps if m["aspectName"] == "glossaryTermInfo" and m["aspect"]["name"] == "WORKS_AT")
    cp = rel_term["aspect"]["customProperties"]
    assert cp["source"] == "Person" and cp["target"] == "Company" and cp["cardinality"] == "MANY_TO_ONE"


def test_emit_dry_run_default():
    mcps = ontology_to_glossary_mcps(_onto())
    result = emit_to_datahub(mcps, dry_run=True)
    assert result["emitted"] is False
    assert result["mode"] == "dry_run"
    assert result["summary"]["mcp_count"] == len(mcps)


def test_emit_without_server_is_dry_run():
    mcps = ontology_to_glossary_mcps(_onto())
    result = emit_to_datahub(mcps, gms_server=None, dry_run=False)
    assert result["emitted"] is False  # no server → dry-run, never crashes


# --- _slug injectivity (seocho-v6w.6): no silent URN collisions ---------------

from seocho.datahub_export import _slug  # noqa: E402


def test_slug_is_injective_for_space_vs_underscore():
    # the original bug: both collapsed to "Total_Revenue"
    assert _slug("Total Revenue") != _slug("Total_Revenue")


def test_slug_distinguishes_distinct_specials():
    variants = ["Total Revenue", "Total_Revenue", "Total-Revenue", "Total.Revenue",
                "Total/Revenue", "Total  Revenue", "Total(Revenue)"]
    slugs = [_slug(v) for v in variants]
    assert len(set(slugs)) == len(variants)  # all distinct → injective


def test_slug_output_is_urn_safe():
    import string
    safe = set(string.ascii_letters + string.digits + "._-")
    for v in ["Total Revenue", "매출", "a/b:c", "x_y"]:
        assert set(_slug(v)) <= safe


def test_slug_stable_and_idempotent():
    assert _slug("Total Revenue") == _slug("Total Revenue")  # deterministic


def test_slug_passes_plain_alnum_labels_unchanged():
    # class labels without '_' keep their existing URN (backward compatible)
    assert _slug("Person") == "Person"
    assert _slug("po.Person") == "po.Person"
    assert _slug("Financial-Metric") == "Financial-Metric"
