"""Infra-free review sheet (seocho-v6w.8): the Docker-free review path shares the
same term_records backend as the DataHub round-trip."""
from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import (
    apply_mapping_spec,
    parse_review_sheet,
    render_review_sheet,
)
from seocho.datahub_export import datahub_glossary_to_mapping_spec


_CLUSTERS = [
    {"surface": "Shetland pony", "frequency": 12, "examples": ["a shetland pony breed"],
     "candidate_labels": []},
    {"surface": "EBITDA", "frequency": 7, "examples": ["adjusted EBITDA"],
     "candidate_labels": ["FinancialMetric"]},
]


def test_render_is_valid_yaml_and_round_trips():
    sheet = render_review_sheet(_CLUSTERS, ontology_name="pets")
    records = parse_review_sheet(sheet)
    names = {r["name"] for r in records}
    assert names == {"Shetland pony", "EBITDA"}
    # rendered defaults: no candidate → new_class; candidate → alias
    by_name = {r["name"]: r for r in records}
    assert by_name["Shetland pony"]["action"] == "new_class"
    assert by_name["EBITDA"]["action"] == "alias"
    assert by_name["EBITDA"]["target"] == "FinancialMetric"
    # every term starts PROPOSED (nothing approved by default → nothing flows back)
    assert all(r["review_status"] == "PROPOSED" for r in records)


def test_reviewer_approval_flows_through_shared_backend():
    onto = Ontology("biz", version="1.0.0", nodes={
        "Concept": NodeDef(description="A concept."),
        "FinancialMetric": NodeDef(description="A metric.", properties={"name": P(str, unique=True)}),
    })
    sheet = render_review_sheet(_CLUSTERS, ontology_name=onto.name)
    # simulate a reviewer editing the YAML: approve EBITDA as an alias
    edited = sheet.replace(
        '  - name: "EBITDA"\n    status: PROPOSED', '  - name: "EBITDA"\n    status: APPROVED')
    records = parse_review_sheet(edited)
    # same contract the DataHub pull produces
    spec = datahub_glossary_to_mapping_spec(records, only_status="APPROVED", ontology_name=onto.name)
    new_onto = apply_mapping_spec(onto, spec)
    assert "EBITDA" in new_onto.nodes["FinancialMetric"].aliases   # approved alias applied
    assert "ShetlandPony" not in new_onto.nodes                     # still PROPOSED → skipped


def test_context_is_not_a_mapping_field():
    records = parse_review_sheet(render_review_sheet(_CLUSTERS, ontology_name="x"))
    assert all("context" not in r for r in records)


def test_empty_and_malformed_clusters_are_skipped():
    sheet = render_review_sheet([{"surface": ""}, {"frequency": 3}, {"surface": "Ok"}],
                                ontology_name="x")
    records = parse_review_sheet(sheet)
    assert [r["name"] for r in records] == ["Ok"]


def test_multiline_example_stays_a_comment():
    """Quarantine context preserves newlines; the sheet must not emit the
    second line uncommented (invalid YAML, loud crash at apply time)."""
    clusters = [{"surface": "Shetland pony", "frequency": 3,
                 "examples": ["first line of context\nSECOND RAW LINE"],
                 "candidate_labels": []}]
    sheet = render_review_sheet(clusters)
    assert "SECOND RAW LINE" not in sheet
    assert "# e.g. first line of context" in sheet
    # and the sheet still round-trips
    assert parse_review_sheet(sheet)[0]["name"] == "Shetland pony"
