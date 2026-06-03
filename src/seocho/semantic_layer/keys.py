"""Deterministic Observation identity (ADR-0103, semantic layer).

The single function the extraction *writer* and the query *reader* both call so
that independent per-chunk extractions of the same reported fact converge onto
ONE graph node instead of fragmenting. Identity is a deterministic hash of
already-canonical components — NOT a free-text name (the defect this replaces).

Inputs MUST already be canonical: entity_key a CIK, concept_id a closed-vocab
metric concept, period_key a normalized period (see periods.normalize_period),
unit an ISO-ish symbol. This function does no fuzzy matching — canonicalization
happens upstream in concepts/periods/identity.
"""

from __future__ import annotations

import hashlib

# Field separator that cannot appear in any canonical component.
_SEP = ""


def observation_key(
    *,
    entity_key: str,
    concept_id: str,
    period_key: str,
    unit: str,
    basis: str = "consolidated",
    workspace_id: str = "",
) -> str:
    """Return a stable ``obs:<hash>`` identity for a reported observation.

    Same canonical inputs → same key, always; different period/concept/entity →
    different key. Used as the MERGE target so re-ingestion and cross-chunk
    extraction are idempotent.
    """
    parts = [
        workspace_id.strip(),
        entity_key.strip(),
        concept_id.strip(),
        period_key.strip(),
        unit.strip().upper(),
        basis.strip().lower(),
    ]
    raw = _SEP.join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"obs:{digest}"
