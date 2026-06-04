"""Unit tests for the XBRL companyfacts ingester (ADR-0103 follow-up) — pure."""

from __future__ import annotations

from seocho.index.xbrl_ingest import companyfacts_to_observations
from seocho.semantic_layer import default_registry, observation_key


def _facts():
    """Synthetic companyfacts: Revenues + NetIncomeLoss, annual frames."""
    return {
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"frame": "CY2024", "form": "10-K", "end": "2024-09-28", "val": 391035000000},
                {"frame": "CY2023", "form": "10-K", "end": "2023-09-30", "val": 383285000000},
                {"frame": "CY2024Q3", "form": "10-Q", "end": "2024-06-29", "val": 9},  # ignored
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                {"frame": "CY2024", "form": "10-K", "end": "2024-09-28", "val": 93736000000},
            ]}},
            "CostOfGoodsSold": {"units": {"USD": [   # not in the closed vocab → ignored
                {"frame": "CY2024", "form": "10-K", "end": "2024-09-28", "val": 210352000000},
            ]}},
        }},
    }


def test_companyfacts_to_observations_reifies_mapped_concepts():
    nodes, rels = companyfacts_to_observations(
        _facts(), registry=default_registry(), cik="0000320193", workspace_id="ws", n_years=5)
    company = [n for n in nodes if n["label"] == "Company"]
    obs = [n for n in nodes if n["label"] == "Observation"]
    assert company[0]["properties"]["cik"] == "0000320193"
    by = {(o["properties"]["concept_id"], o["properties"]["period_key"]): o["properties"]
          for o in obs}
    assert by[("metric:Revenue", "fiscal:2024:FY")]["value_num"] == 391035000000.0
    assert by[("metric:Revenue", "fiscal:2023:FY")]["value_num"] == 383285000000.0
    assert by[("metric:NetIncome", "fiscal:2024:FY")]["value_num"] == 93736000000.0
    # CostOfGoodsSold is out of the closed vocab — never reified
    assert all(p["concept_id"] in ("metric:Revenue", "metric:NetIncome") for p in by.values())
    # quarterly frame ignored (annual only)
    assert ("metric:Revenue", "fiscal:2024:Q3") not in by


def test_observation_id_matches_canonical_key():
    nodes, _ = companyfacts_to_observations(
        _facts(), registry=default_registry(), cik="0000320193", workspace_id="ws")
    rev24 = next(n for n in nodes if n["label"] == "Observation"
                 and n["properties"]["period_key"] == "fiscal:2024:FY"
                 and n["properties"]["concept_id"] == "metric:Revenue")
    assert rev24["properties"]["obs_id"] == observation_key(
        entity_key="0000320193", concept_id="metric:Revenue",
        period_key="fiscal:2024:FY", unit="USD", workspace_id="ws")


def test_has_observation_edges_connect_company_to_each_observation():
    nodes, rels = companyfacts_to_observations(
        _facts(), registry=default_registry(), cik="0000320193")
    obs_ids = {n["id"] for n in nodes if n["label"] == "Observation"}
    assert rels and all(r["type"] == "HAS_OBSERVATION" and r["source"] == "cik:0000320193"
                        and r["target"] in obs_ids for r in rels)
    assert len(rels) == len(obs_ids)


def test_min_fiscal_year_filter_and_alias_dedup():
    nodes, _ = companyfacts_to_observations(
        _facts(), registry=default_registry(), cik="c", min_fiscal_year=2024)
    years = {n["properties"]["period_key"] for n in nodes if n["label"] == "Observation"}
    assert "fiscal:2023:FY" not in years and "fiscal:2024:FY" in years


def test_xbrl_map_and_resolve_xbrl():
    reg = default_registry()
    assert reg.resolve_xbrl("Revenues") == "metric:Revenue"
    assert reg.resolve_xbrl("us-gaap:NetIncomeLoss") == "metric:NetIncome"
    assert reg.resolve_xbrl("CostOfGoodsSold") is None
    assert "Revenues" in reg.xbrl_map and "us-gaap:Revenues" not in reg.xbrl_map
