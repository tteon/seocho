"""Reified Observation writer (ADR-0103, slice S3).

After per-chunk extraction produces ``{id,label,properties}`` nodes, this
canonicalizes the metric-like ones into reified :Observation nodes keyed
deterministically (the same `observation_key` the reader matches on), plus a
canonical :Company node (keyed by CIK) and a :HAS_OBSERVATION edge — emitted
ALONGSIDE the existing nodes (additive). Because the obs_id is a deterministic
function of (CIK, concept, period, unit), independent chunks that report the
same fact MERGE onto one node instead of fragmenting.

Pure transform (`build_observations`) so it unit-tests without a graph; the
pipeline hook is flag-gated by `SEOCHO_SEMANTIC_LAYER` (default off).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..semantic_layer import normalize_period, observation_key
from ..semantic_layer.concepts import ConceptRegistry
from ..semantic_layer.identity import EntityResolver

_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)?", re.I)


def semantic_layer_enabled() -> bool:
    return str(os.environ.get("SEOCHO_SEMANTIC_LAYER", "")).strip().lower() in ("1", "true", "yes")


def _to_value_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).lower())
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return num * _SCALE.get((m.group(2) or "").lower(), 1.0)


def _is_metric_node(props: Dict[str, Any]) -> bool:
    return "value" in props or "value_num" in props


def _linked_entity(metric_id: Any, rels: List[Dict[str, Any]],
                   by_id: Dict[Any, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find an entity node connected to the metric node via any relationship."""
    for r in rels:
        src, tgt = r.get("source"), r.get("target")
        other = None
        if src == metric_id:
            other = tgt
        elif tgt == metric_id:
            other = src
        if other is None:
            continue
        node = by_id.get(other)
        if not node:
            continue
        nprops = node.get("properties") or {}
        if not _is_metric_node(nprops) and (nprops.get("name") or nprops.get("ticker")):
            return node
    return None


def build_observations(
    nodes: List[Dict[str, Any]],
    rels: List[Dict[str, Any]],
    *,
    registry: ConceptRegistry,
    resolver: EntityResolver,
    workspace_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Transform extracted nodes/rels → reified (Company, Observation, edge) lists.

    A metric node reifies only when concept, entity→CIK, period, and a numeric
    value all resolve; otherwise it is left as-is (no Observation emitted).
    """
    by_id = {n.get("id"): n for n in nodes}
    obs_nodes: List[Dict[str, Any]] = []
    obs_rels: List[Dict[str, Any]] = []
    seen_company: set = set()
    seen_obs: set = set()

    for n in nodes:
        props = n.get("properties") or {}
        if not _is_metric_node(props):
            continue
        concept_id = (registry.resolve(str(n.get("label", "")))
                      or registry.resolve(str(props.get("name", ""))))
        if not concept_id:
            continue
        period_key = normalize_period(
            str(props.get("period") or props.get("year") or props.get("name", "")))
        value_num = _to_value_num(props.get("value", props.get("value_num")))
        if not (period_key and value_num is not None):
            continue
        entity = _linked_entity(n.get("id"), rels, by_id)
        if not entity:
            continue
        eprops = entity.get("properties") or {}
        cik = (resolver.resolve(str(eprops.get("ticker") or ""))
               or resolver.resolve(str(eprops.get("name") or entity.get("id") or "")))
        if not cik:
            continue
        unit = str(props.get("unit") or "USD")
        obs_id = observation_key(entity_key=cik, concept_id=concept_id,
                                 period_key=period_key, unit=unit,
                                 workspace_id=workspace_id)
        if obs_id in seen_obs:
            continue
        seen_obs.add(obs_id)
        company_id = f"cik:{cik}"
        if company_id not in seen_company:
            seen_company.add(company_id)
            obs_nodes.append({
                "id": company_id, "label": "Company",
                "properties": {"cik": cik, "name": eprops.get("name") or cik},
            })
        obs_nodes.append({
            "id": obs_id, "label": "Observation",
            "properties": {
                "obs_id": obs_id, "concept_id": concept_id, "entity_cik": cik,
                "period_key": period_key, "value_num": value_num,
                "unit": unit, "basis": "consolidated",
            },
        })
        obs_rels.append({"source": company_id, "target": obs_id,
                         "type": "HAS_OBSERVATION", "properties": {}})
    return obs_nodes, obs_rels


def ensure_observation_constraint(graph_store: Any, database: str = "neo4j") -> bool:
    """Create the UNIQUE constraint on Observation.obs_id + an index on
    Company.cik. Idempotent (IF NOT EXISTS). Returns True on success.

    The constraint dedups Observations and backs the obs_id seek; the Company
    .cik index lets the exact-key compiled Cypher start with a NodeIndexSeek on
    the anchor entity rather than a label scan — so the structured path is O(1),
    not O(all companies). Both are validated by the PROFILE profiler (S12).
    """
    try:
        with graph_store._driver.session(database=database) as session:
            session.run(
                "CREATE CONSTRAINT seocho_observation_obs_id IF NOT EXISTS "
                "FOR (o:Observation) REQUIRE o.obs_id IS UNIQUE"
            )
            session.run(
                "CREATE INDEX seocho_company_cik IF NOT EXISTS "
                "FOR (c:Company) ON (c.cik)"
            )
        return True
    except Exception:
        return False
