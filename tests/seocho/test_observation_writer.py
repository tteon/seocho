"""Unit tests for the reified Observation writer (ADR-0103 S3) — pure transform.

Feeds synthetic extracted {id,label,properties} nodes + relationships and
asserts build_observations emits deterministically-keyed :Observation +
:Company(cik) + :HAS_OBSERVATION, skipping anything that can't fully reify.
No graph, no LLM.
"""

from __future__ import annotations

from seocho.index.observation_writer import _to_value_num, build_observations
from seocho.semantic_layer import default_registry, default_resolver, observation_key


def _rr():
    return default_registry(), default_resolver()


def _extracted(metric_label="Revenue", value="391035000000", period="FY2024",
               company="Apple Inc."):
    nodes = [
        {"id": "apple", "label": "Company", "properties": {"name": company}},
        {"id": "rev_2024", "label": metric_label,
         "properties": {"name": f"Total Revenue {period}", "value": value, "period": period}},
    ]
    rels = [{"source": "apple", "target": "rev_2024", "type": "REPORTED", "properties": {}}]
    return nodes, rels


def test_build_observations_reifies_company_and_observation():
    reg, res = _rr()
    nodes, rels = _extracted()
    obs_nodes, obs_rels = build_observations(nodes, rels, registry=reg, resolver=res,
                                             workspace_id="ws")
    labels = {n["label"] for n in obs_nodes}
    assert labels == {"Company", "Observation"}
    company = next(n for n in obs_nodes if n["label"] == "Company")
    obs = next(n for n in obs_nodes if n["label"] == "Observation")
    assert company["properties"]["cik"] == "0000320193"
    assert obs["properties"]["concept_id"] == "metric:Revenue"
    assert obs["properties"]["entity_cik"] == "0000320193"
    assert obs["properties"]["period_key"] == "fiscal:2024:FY"
    assert obs["properties"]["value_num"] == 391035000000.0
    # deterministic obs_id == standalone key (writer/reader share derivation)
    assert obs["properties"]["obs_id"] == observation_key(
        entity_key="0000320193", concept_id="metric:Revenue",
        period_key="fiscal:2024:FY", unit="USD", workspace_id="ws")
    # edge connects the cik-Company to the Observation
    assert obs_rels == [{"source": "cik:0000320193", "target": obs["id"],
                         "type": "HAS_OBSERVATION", "properties": {}}]


def test_build_observations_generic_label_resolves_via_name():
    reg, res = _rr()
    # label is the abstract "FinancialMetric"; concept comes from the name
    nodes, rels = _extracted(metric_label="FinancialMetric")
    nodes[1]["properties"]["name"] = "net income"
    obs = [n for n in build_observations(nodes, rels, registry=reg, resolver=res)[0]
           if n["label"] == "Observation"]
    assert obs and obs[0]["properties"]["concept_id"] == "metric:NetIncome"


def test_build_observations_dedups_same_fact():
    reg, res = _rr()
    nodes, rels = _extracted()
    # a second chunk re-states the same fact with a different free-text name/id
    nodes.append({"id": "rev_again", "label": "Revenue",
                  "properties": {"name": "net sales fiscal 2024", "value": "391035000000",
                                 "period": "FY2024"}})
    rels.append({"source": "apple", "target": "rev_again", "type": "REPORTED"})
    obs_nodes, _ = build_observations(nodes, rels, registry=reg, resolver=res)
    observations = [n for n in obs_nodes if n["label"] == "Observation"]
    assert len(observations) == 1   # deterministic key collapses the duplicate


def test_build_observations_skips_unreifiable():
    reg, res = _rr()
    # unknown company -> no CIK -> skip
    nodes, rels = _extracted(company="Nonexistent Holdings")
    assert build_observations(nodes, rels, registry=reg, resolver=res) == ([], [])
    # no period -> skip
    n2, r2 = _extracted()
    del n2[1]["properties"]["period"]
    n2[1]["properties"]["name"] = "Total Revenue"
    assert build_observations(n2, r2, registry=reg, resolver=res) == ([], [])
    # not a metric node (no value) -> nothing
    assert build_observations(
        [{"id": "x", "label": "Company", "properties": {"name": "Apple Inc."}}],
        [], registry=reg, resolver=res) == ([], [])


def test_to_value_num_scale_aware():
    assert _to_value_num("391035000000") == 391035000000.0
    assert _to_value_num("$391,035 million") == 391035000000.0
    assert _to_value_num(2100000000) == 2100000000.0
    assert _to_value_num("n/a") is None
    assert _to_value_num(None) is None
