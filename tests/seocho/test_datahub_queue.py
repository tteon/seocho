"""The ambiguity review queue surfaced in DataHub as PROPOSED terms (seocho-v6w.2).

Offline: builds proposals from quarantine clusters and checks the dry-run emit
shape. Live emit is covered by the env-guarded live gate.
"""
from __future__ import annotations

from seocho.datahub_export import (
    ambiguity_clusters_to_glossary_proposals,
    emit_to_datahub,
)


_CLUSTERS = [
    {"surface": "Shetland pony", "frequency": 12, "signals": {"oov": 12},
     "candidate_labels": [], "examples": ["a shetland pony breed"]},
    {"surface": "EBITDA", "frequency": 7, "signals": {"alias_collision": 7},
     "candidate_labels": ["FinancialMetric"], "examples": []},
]


def test_clusters_become_proposed_terms_under_proposed_node():
    mcps = ambiguity_clusters_to_glossary_proposals(_CLUSTERS, package_id="demo")
    nodes = [m for m in mcps if m["entityType"] == "glossaryNode"]
    terms = [m for m in mcps if m["entityType"] == "glossaryTerm"]
    assert any(n["aspect"].get("name", "").startswith("demo — Proposed") for n in nodes)
    assert {t["aspect"]["name"] for t in terms} == {"Shetland pony", "EBITDA"}
    for t in terms:
        assert t["aspect"]["customProperties"]["review_status"] == "PROPOSED"
        assert "frequency" in t["aspect"]["customProperties"]


def test_dry_run_emit_reports_shape_without_server():
    mcps = ambiguity_clusters_to_glossary_proposals(_CLUSTERS, package_id="demo")
    res = emit_to_datahub(mcps, gms_server=None, dry_run=True)
    assert res["emitted"] is False
    assert res["mode"] == "dry_run"
    assert res["summary"]["glossary_terms"] == 2


def test_empty_quarantine_yields_only_the_proposed_node():
    mcps = ambiguity_clusters_to_glossary_proposals([], package_id="demo")
    assert [m["entityType"] for m in mcps] == ["glossaryNode"]  # the container, no terms
