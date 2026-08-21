"""Offline tests for the DataHub glossary pull normalization (seocho-v6w.3) and
the structuredProperty definition bootstrap (seocho-v6w.2).

The GraphQL HTTP call is not exercised here (that is the env-guarded live gate);
these test the pure normalization from a GraphQL-shaped entity into the neutral
``term_records`` contract, and that the round-trip into the ontology works.
"""
from __future__ import annotations

from seocho.connectors.datahub import (
    DEFAULT_APPROVED_TAG,
    glossary_term_to_record,
)
from seocho.datahub_export import (
    datahub_glossary_to_mapping_spec,
    scorecard_structured_property_definitions,
)
from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import apply_mapping_spec


def _term(name, definition="", *, approved=False, parent=None):
    """A GraphQL glossaryTerm entity as the search query returns it."""
    entity = {
        "urn": f"urn:li:glossaryTerm:demo.{name}",
        "type": "GLOSSARY_TERM",
        "name": name,
        "properties": {"name": name, "description": definition},
        "tags": {"tags": [{"tag": {"urn": f"urn:li:tag:{DEFAULT_APPROVED_TAG}",
                                   "name": DEFAULT_APPROVED_TAG}}]} if approved else {"tags": []},
    }
    if parent:
        entity["isRelatedTerms"] = {"relationships": [
            {"entity": {"urn": f"urn:li:glossaryTerm:demo.{parent}", "properties": {"name": parent}}}]}
    return entity


def test_approved_tag_sets_status():
    rec = glossary_term_to_record(_term("Animal", "A creature.", approved=True))
    assert rec["review_status"] == "APPROVED"
    assert rec["description"] == "A creature."


def test_missing_tag_is_proposed():
    rec = glossary_term_to_record(_term("Animal", "A creature.", approved=False))
    assert rec["review_status"] == "PROPOSED"


def test_existing_class_becomes_annotate():
    rec = glossary_term_to_record(_term("Animal", "edited def", approved=True),
                                  known_labels=frozenset({"Animal"}))
    assert rec["action"] == "annotate"
    assert rec["target"] == "Animal"


def test_new_term_becomes_new_class_with_parent():
    rec = glossary_term_to_record(_term("ShetlandPony", "a pony", approved=True, parent="Breed"),
                                  known_labels=frozenset({"Animal", "Breed"}))
    assert rec["action"] == "new_class"
    assert rec["parent"] == "Breed"


def test_pull_records_apply_end_to_end():
    onto = Ontology("pets", package_id="demo", version="1.0.0", nodes={
        "Animal": NodeDef(description="", properties={"name": P(str, unique=True)}),
        "Breed": NodeDef(description="A breed.", properties={"name": P(str, unique=True)}, broader=["Animal"]),
    })
    known = frozenset(onto.nodes)
    entities = [
        _term("ShetlandPony", "A hardy pony.", approved=True, parent="Breed"),  # new_class
        _term("Habitat", "where it lives", approved=False),                    # PROPOSED → skip
    ]
    records = [glossary_term_to_record(e, known_labels=known) for e in entities]
    spec = datahub_glossary_to_mapping_spec(records, only_status="APPROVED", ontology_name=onto.name)
    new_onto = apply_mapping_spec(onto, spec)
    assert "ShetlandPony" in new_onto.nodes
    assert new_onto.nodes["ShetlandPony"].broader == ["Breed"]
    assert "Habitat" not in new_onto.nodes  # unapproved never flows back


def test_pull_emits_annotate_for_edited_existing_class():
    """The pull adapter marks a definition edit on an existing class as
    'annotate'; the record is correctly shaped for apply_mapping_spec. (The
    apply-side 'annotate' action ships in seocho-v6w.9 / PR #598 — until it
    lands, datahub_glossary_to_mapping_spec drops the record rather than
    misapplying it, so no existing class is silently mutated here.)"""
    rec = glossary_term_to_record(
        _term("Animal", "A living creature in the corpus.", approved=True),
        known_labels=frozenset({"Animal"}))
    assert rec == {"name": "Animal", "review_status": "APPROVED", "action": "annotate",
                   "target": "Animal", "description": "A living creature in the corpus."}


# --- structuredProperty definitions bootstrap (seocho-v6w.2) -----------------


def test_property_definitions_cover_fixed_and_dimensions():
    mcps = scorecard_structured_property_definitions(["taxonomy_health", "corpus_coverage"])
    urns = {m["entityUrn"] for m in mcps}
    assert "urn:li:structuredProperty:seocho.scorecard.overall_score" in urns
    assert "urn:li:structuredProperty:seocho.scorecard.grade" in urns
    assert "urn:li:structuredProperty:seocho.scorecard.blocking" in urns
    assert "urn:li:structuredProperty:seocho.scorecard.taxonomy_health" in urns
    assert "urn:li:structuredProperty:seocho.scorecard.corpus_coverage" in urns
    for m in mcps:
        assert m["entityType"] == "structuredProperty"
        assert m["aspectName"] == "propertyDefinition"
        assert m["aspect"]["valueType"].startswith("urn:li:dataType:")
        assert m["aspect"]["entityTypes"]  # non-empty


def test_definition_urns_match_value_urns():
    from seocho.datahub_export import scorecard_to_structured_properties
    sc = {"overall_score": 0.9, "grade": "A", "blocking": False,
          "dimensions": [{"name": "taxonomy_health", "score": 0.8}]}
    def_urns = {m["entityUrn"] for m in scorecard_structured_property_definitions(["taxonomy_health"])}
    value_urns = {p["propertyUrn"]
                  for p in scorecard_to_structured_properties(sc, target_urn="urn:li:glossaryNode:demo")[0]
                  ["aspect"]["properties"]}
    # every value assigned has a definition (so live emit won't be rejected)
    assert value_urns <= def_urns


def test_blocking_value_is_stringified():
    from seocho.datahub_export import scorecard_to_structured_properties
    props = scorecard_to_structured_properties(
        {"overall_score": 0.9, "grade": "A", "blocking": True, "dimensions": []},
        target_urn="urn:li:glossaryNode:demo")[0]["aspect"]["properties"]
    blocking = next(p for p in props if p["propertyUrn"].endswith("blocking"))
    # tagged union (PDL PrimitivePropertyValue) — raw scalars fail GMS validation
    assert blocking["values"] == [{"string": "true"}]


def test_pull_scopes_to_the_package_urn_prefix(monkeypatch):
    """A GMS holding two SEOCHO ontologies must not leak ontology B's approved
    terms into ontology A's apply: urn_prefix keeps only this package's terms."""
    import seocho.connectors.datahub as dh
    from seocho.datahub_export import package_term_urn_prefix

    ours = _term("Animal", "A creature.", approved=True)
    foreign = _term("Revenue", "Other ontology.", approved=True)
    foreign["urn"] = "urn:li:glossaryTerm:otherpkg.Revenue"

    monkeypatch.setattr(
        dh.DataHubGraphQLClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(
        dh.DataHubGraphQLClient, "iter_glossary_terms",
        lambda self, **kw: iter([ours, foreign]))

    recs = dh.fetch_glossary_term_records(
        server="http://gms:8080", urn_prefix=package_term_urn_prefix("demo"))
    assert [r["name"] for r in recs] == ["Animal"]

    unscoped = dh.fetch_glossary_term_records(server="http://gms:8080")
    assert [r["name"] for r in unscoped] == ["Animal", "Revenue"]
