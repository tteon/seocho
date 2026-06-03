"""Deterministic Observation-lookup compiler (ADR-0103).

Turns fully-resolved `ObservationSlots` into exact-match Cypher — the Phase-2
"deterministic compilation" that replaces free Cypher generation. Every
predicate is `=` / `IN` on a typed/keyed property (concept_id, period_key,
cik); NO `CONTAINS`, no fuzzy matching. Labels are static literals (read-safe,
no dynamic-label interpolation); all values are bound parameters.

The query reader and the DCC probe both use this, so "does the structure carry
the answer" is tested on the exact Cypher the lane will run.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .slots import ObservationSlots

# Static, parameterized, read-only. Matches an entity by canonical CIK, then its
# reified Observations by closed-vocab concept + canonical period + basis.
_OBSERVATION_LOOKUP = (
    "MATCH (c:Company {cik: $cik})-[:HAS_OBSERVATION]->(o:Observation)\n"
    "WHERE o.concept_id = $concept_id\n"
    "  AND o.period_key IN $period_keys\n"
    "  AND o.basis = $basis\n"
    "  AND ($workspace_id = '' OR o.workspace_id = $workspace_id)\n"
    "RETURN o.value_num AS value, o.unit AS unit, o.period_key AS period,\n"
    "       o.concept_id AS concept_id, o.obs_id AS obs_id\n"
    "ORDER BY o.period_key\n"
    "LIMIT $limit"
)


def compile_observation_lookup(
    slots: ObservationSlots,
    *,
    workspace_id: str = "",
    limit: int = 20,
) -> Tuple[str, Dict[str, Any]]:
    """Compile resolved slots → (cypher, params) for an exact-key metric lookup.

    Raises ValueError if the slots are not fully resolved — the arbiter must
    route unresolved slots to CLARIFY/FAIL, never to compilation.
    """
    if not slots.is_fully_resolved:
        raise ValueError(
            f"cannot compile unresolved slots (unresolved={slots.unresolved}, "
            f"entity_cik={slots.entity_cik!r}, concept_id={slots.concept_id!r}, "
            f"period_keys={slots.period_keys!r})"
        )
    params: Dict[str, Any] = {
        "cik": slots.entity_cik,
        "concept_id": slots.concept_id,
        "period_keys": list(slots.period_keys),
        "basis": slots.basis,
        "workspace_id": workspace_id,
        "limit": limit,
    }
    return _OBSERVATION_LOOKUP, params
