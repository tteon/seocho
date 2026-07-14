"""Deterministic XBRL companyfacts → Observation ingester (ADR-0103 follow-up).

S11 showed HTML-table scraping lifts the real-filing floor but is fragile
(column↔year misalignment, wrong-row selection). SEC publishes the same Item-8
financial statements as STRUCTURED XBRL via the ``companyfacts`` API — fiscal
frames, typed values, no LLM extraction, no HTML parsing. This is the correct
production ingestion path: map us-gaap concept tags to the closed
ConceptRegistry vocabulary and reify each annual fact into a deterministically
keyed :Observation (same `observation_key` the reader matches on).

`companyfacts_to_observations` is a pure transform (unit-tested without
network); `fetch_companyfacts` is the only impure surface.

Honest note: SEC XBRL is also the source of the prior-resistant benchmark gold,
so ingesting from it is NOT a benchmark win — it is the deterministic mechanism
that removes the extraction noise S11 measured.
"""

from __future__ import annotations

import json
import re
import os
import urllib.request
from typing import Any, Dict, List, Tuple

from ..semantic_layer import Period, observation_key
from ..semantic_layer.concepts import ConceptRegistry

USER_AGENT = os.environ.get("SEC_USER_AGENT", "seocho-ingest support@seocho.io")
# Duration (income-statement) full-year frame: CY2024.
_DURATION_FRAME_RE = re.compile(r"^CY(\d{4})$")
# Instant (balance-sheet) fiscal-year-end frame: CY2024Q3I (Apple, Sept FYE),
# CY2024Q4I (calendar FYE), etc. The CY year == fiscal year for the FY-end snap.
_INSTANT_FRAME_RE = re.compile(r"^CY(\d{4})Q\dI$")
# unit preference: USD (currency), then USD/shares (EPS), then anything declared.
_UNIT_PREF = ("USD", "USD/shares")


def _pick_unit(units: Dict[str, Any]) -> Optional[str]:
    for u in _UNIT_PREF:
        if u in units:
            return u
    return next(iter(units), None)


def _select_annual(units: Dict[str, Any], n_years: int, *,
                   instant: bool = False) -> List[Dict[str, Any]]:
    """Most-recent n_years annual 10-K facts (one per fiscal year).

    Duration metrics use the full-year frame (CY2024); instant (balance-sheet)
    metrics use the fiscal-year-END instant frame (CY2024Q?I) — the gap the
    dataset generator skipped. First record per fiscal year wins.
    """
    unit_key = _pick_unit(units)
    if unit_key is None:
        return []
    frame_re = _INSTANT_FRAME_RE if instant else _DURATION_FRAME_RE
    out: Dict[int, Dict[str, Any]] = {}
    for rec in units[unit_key]:
        m = frame_re.match(str(rec.get("frame") or ""))
        if not m or rec.get("form") != "10-K":
            continue
        fy = int(m.group(1))
        if fy in out:
            continue
        out[fy] = {"fiscal_year": fy, "value": rec.get("val"), "unit": unit_key,
                   "period_end": rec.get("end")}
    return sorted(out.values(), key=lambda r: r["fiscal_year"], reverse=True)[:n_years]


def companyfacts_to_observations(
    facts_json: Dict[str, Any],
    *,
    registry: ConceptRegistry,
    cik: str,
    workspace_id: str = "",
    n_years: int = 5,
    min_fiscal_year: int = 0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Transform an SEC companyfacts payload → reified (Company, Observation) lists.

    For each us-gaap tag that maps into the closed ConceptRegistry vocabulary,
    select annual facts and reify them. Tags are tried in registry order so the
    earliest concept tag wins a fiscal year (dedup across alias tags).
    """
    usgaap = (facts_json.get("facts", {}) or {}).get("us-gaap", {}) or {}
    company_name = facts_json.get("entityName") or cik
    company_id = f"cik:{cik}"
    obs_nodes: List[Dict[str, Any]] = [
        {"id": company_id, "label": "Company",
         "properties": {"cik": cik, "name": company_name}}
    ]
    obs_rels: List[Dict[str, Any]] = []
    seen_year_concept: set = set()
    seen_obs: set = set()

    for tag, concept_id in registry.xbrl_map.items():
        node = usgaap.get(tag)
        if not node:
            continue
        concept = registry.get(concept_id)
        is_instant = bool(concept and concept.period_type == "instant")
        for fact in _select_annual(node.get("units", {}), n_years, instant=is_instant):
            fy = fact["fiscal_year"]
            if fy < min_fiscal_year:
                continue
            if (concept_id, fy) in seen_year_concept:   # alias tag already filled
                continue
            value, unit = fact["value"], fact["unit"]
            if not isinstance(value, (int, float)):
                continue
            seen_year_concept.add((concept_id, fy))
            period_key = Period(fiscal_year=fy).key
            obs_id = observation_key(entity_key=cik, concept_id=concept_id,
                                     period_key=period_key, unit=unit,
                                     workspace_id=workspace_id)
            if obs_id in seen_obs:
                continue
            seen_obs.add(obs_id)
            obs_nodes.append({
                "id": obs_id, "label": "Observation",
                "properties": {
                    "obs_id": obs_id, "concept_id": concept_id, "entity_cik": cik,
                    "period_key": period_key, "period_end": fact.get("period_end") or "",
                    "value_num": float(value), "unit": unit, "basis": "consolidated",
                    # companyfacts annual frames are consolidated GAAP, not restated
                    # (ADR-0103 H4 dimensions, first-class).
                    "segment": "consolidated", "is_restated": False,
                },
            })
            obs_rels.append({"source": company_id, "target": obs_id,
                             "type": "HAS_OBSERVATION", "properties": {}})
    return obs_nodes, obs_rels


def fetch_companyfacts(cik: str) -> Dict[str, Any]:
    """Fetch the SEC XBRL companyfacts payload for a 10-digit CIK (impure)."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)
