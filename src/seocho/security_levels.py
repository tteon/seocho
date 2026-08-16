"""Palantir-Ontology-style layered security (organ 3): dataset → row → cell → sub-cell.

hadry's model: PostgreSQL is the ground truth where security is authored *semantically*
(domain-driven, on the ontology), and the graph is a governed projection carrying the
classification forward. Security is layered at four granularities (the Palantir Ontology
model), each a real mechanism, each expressed as a sensitivity on the ontology rather than
ad-hoc app code:

- **Level 0 — dataset-backed**: the baseline. In SEOCHO this is the workspace
  (``_workspace_id`` + ADR-0164 enforce_workspace_filter) — a whole dataset visible or not.
- **Row-wise (OSP / Restricted View)**: a whole record (a fact / object) visible per the
  principal's clearance — the RLS policy of ADR-0211.
- **Cell-level (row × column)**: a specific property within a visible row masked — this is
  exactly ``risk/preflight.OntologyDisclosurePolicy.filter_record`` (per-property
  classification vs role clearance); reused, not re-implemented.
- **Sub-cell (derived property)**: the most granular — protect individual ELEMENTS within an
  array-valued property (e.g. one sensitive note inside a list of notes), returning a derived
  property that keeps only the elements the principal may see. This is the piece the
  field-level ``filter_record`` cannot express, and the one this module adds.

The lattice is the shared ``public < internal < restricted < secret``; a principal with
clearance C sees a thing classified S iff ``rank(S) <= rank(C)`` — default-DENY for unknowns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# The single sensitivity lattice, shared with risk/preflight.OntologyDisclosurePolicy.
from .risk.preflight import _CLASSIFICATION as _RANK

DEFAULT_SENSITIVITY = "restricted"     # default-DENY for anything unclassified


class SecurityLevel:
    DATASET = "dataset"
    ROW = "row"
    CELL = "cell"
    SUBCELL = "subcell"


def _rank(name: Optional[str]) -> int:
    return _RANK.get((name or DEFAULT_SENSITIVITY), _RANK[DEFAULT_SENSITIVITY])


def visible(sensitivity: Optional[str], clearance: str) -> bool:
    """A thing classified ``sensitivity`` is visible to a principal with ``clearance``
    iff its rank does not exceed the clearance (default-DENY for unknown sensitivity)."""
    return _rank(sensitivity) <= _rank(clearance)


# -- Level 0: dataset ------------------------------------------------------
def dataset_visible(record_workspace: str, principal_workspace: str) -> bool:
    """Baseline: the whole dataset (workspace) — a record of another workspace is invisible."""
    return str(record_workspace) == str(principal_workspace)


# -- Row-wise (OSP / Restricted View) --------------------------------------
def row_visible(row_sensitivity: Optional[str], clearance: str) -> bool:
    """Object Security Policy: a whole record is visible iff its sensitivity is within
    clearance. A denied row is DROPPED (invisible), never returned-but-masked (which would
    leak its existence/cardinality)."""
    return visible(row_sensitivity, clearance)


# -- Sub-cell (derived property over an array) -----------------------------
def filter_array_elements(
    elements: Sequence[Any],
    element_sensitivities: Sequence[Optional[str]],
    clearance: str,
) -> List[Any]:
    """The Palantir sub-cell mechanism: a DERIVED property that keeps only the array
    elements the principal may see. E.g. a list of patient/case notes where one note is
    ``secret`` — a general-staff principal gets the list WITHOUT that element, a compliance
    principal gets all. Elements without a stated sensitivity default-DENY (restricted)."""
    out: List[Any] = []
    for i, el in enumerate(elements):
        s = element_sensitivities[i] if i < len(element_sensitivities) else None
        if visible(s, clearance):
            out.append(el)
    return out


@dataclass
class SecurityPolicy:
    """A semantically-expressed, layered policy over one ontology object type.

    ``row_sensitivity``    — the object's OSP class (row-wise).
    ``property_sensitivity`` — per-property class (cell-level).
    ``array_element_sensitivity`` — per-property, a parallel list of per-element classes
                                    (sub-cell, the derived property).
    """
    row_sensitivity: str = DEFAULT_SENSITIVITY
    property_sensitivity: Mapping[str, str] = field(default_factory=dict)
    array_element_sensitivity: Mapping[str, Sequence[Optional[str]]] = field(default_factory=dict)

    def apply(self, record: Mapping[str, Any], *, clearance: str
              ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """Apply all four levels to one record for a principal ``clearance``. Returns
        ``(visible_record_or_None, redactions)``; ``None`` means the ROW was dropped
        (OSP denied). ``redactions`` names what each level removed, for the audit trail."""
        redactions: List[str] = []
        # Row-wise (OSP): a denied row is dropped whole (existence hidden).
        if not row_visible(self.row_sensitivity, clearance):
            return None, [f"row:{self.row_sensitivity}"]
        out: Dict[str, Any] = {}
        for prop, value in record.items():
            # Cell-level: a property above clearance is masked out of the row.
            if not visible(self.property_sensitivity.get(prop), clearance):
                redactions.append(f"cell:{prop}")
                continue
            # Sub-cell: an array property keeps only the elements within clearance.
            elem_sens = self.array_element_sensitivity.get(prop)
            if elem_sens is not None and isinstance(value, (list, tuple)):
                kept = filter_array_elements(value, elem_sens, clearance)
                if len(kept) != len(value):
                    redactions.append(f"subcell:{prop}:{len(value) - len(kept)}")
                out[prop] = kept
            else:
                out[prop] = value
        return out, redactions
