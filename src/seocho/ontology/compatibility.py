"""Typed ontology-change compatibility classification (seocho-ia4.2).

``diff_ontologies`` (governance.py) marks ANY changed node/relationship as
``breaking`` — so adding an *optional* property is flagged breaking (a false-major:
the schema-registry equivalent of forcing a new subject version for a
backward-compatible field add). This module classifies each change *atom* into
schema-registry compatibility classes, so downstream policy (the freshness read
barrier, seocho-ia4.6) can distinguish drift that actually invalidates an answer
from drift that is reconcilable or irrelevant.

Compatibility vocabulary (producer=writes data, consumer=reads/answers):
- BACKWARD  : old data still valid under the new schema (add optional prop/label,
              loosen cardinality, add alias/description).
- FORWARD   : old readers tolerate new-schema data, but old data may not be valid
              (remove a prop/label a reader referenced).
- BREAKING  : old data may be invalid AND/OR answers may change (add required/unique
              prop, tighten cardinality, retype, narrow domain/range, remove).

For the read barrier we care about **answer invalidation**: BREAKING and FORWARD
(remove) atoms can change an answer; BACKWARD atoms cannot. ``breaking_labels`` /
``breaking_properties`` collect the atoms that can invalidate a served answer.
Cheap, structural, no DL reasoner (that stays offline; see ia4.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

# cardinality multiplicity rank (higher = more permissive)
_CARD_RANK = {
    "ONE_TO_ONE": 0,
    "ONE_TO_MANY": 1,
    "MANY_TO_ONE": 1,
    "MANY_TO_MANY": 2,
}


def _is_strengthening_constraint(c: str) -> bool:
    return str(c or "").upper() in {"REQUIRED", "UNIQUE"}


@dataclass(frozen=True)
class ChangeAtom:
    kind: str                 # node_added, prop_added_optional, prop_retyped, ...
    label: str                # node label or relationship type
    prop: str = ""            # property name, when applicable
    compatibility: str = "BACKWARD"   # BACKWARD | FORWARD | BREAKING
    detail: str = ""

    @property
    def invalidating(self) -> bool:
        """Can this atom invalidate an already-served answer over old data?"""
        return self.compatibility in {"BREAKING", "FORWARD"}


@dataclass
class CompatibilityReport:
    atoms: List[ChangeAtom] = field(default_factory=list)

    @property
    def changed_labels(self) -> Set[str]:
        return {a.label for a in self.atoms}

    @property
    def breaking_labels(self) -> Set[str]:
        """Labels with >=1 answer-invalidating atom (BREAKING/FORWARD)."""
        return {a.label for a in self.atoms if a.invalidating}

    @property
    def breaking_properties(self) -> Set[Tuple[str, str]]:
        """(label, prop) pairs whose change can invalidate an answer that reads them."""
        return {(a.label, a.prop) for a in self.atoms if a.invalidating and a.prop}

    @property
    def overall(self) -> str:
        if any(a.compatibility == "BREAKING" for a in self.atoms):
            return "BREAKING"
        if any(a.compatibility == "FORWARD" for a in self.atoms):
            return "FORWARD"
        if self.atoms:
            return "BACKWARD"
        return "NONE"

    @property
    def is_breaking(self) -> bool:
        return self.overall == "BREAKING"


def _classify_node(label: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[ChangeAtom]:
    atoms: List[ChangeAtom] = []
    old_props = (old or {}).get("properties", {}) or {}
    new_props = (new or {}).get("properties", {}) or {}
    for p in sorted(set(new_props) - set(old_props)):          # added props
        spec = new_props[p] or {}
        if _is_strengthening_constraint(spec.get("constraint", "")):
            atoms.append(ChangeAtom("prop_added_required", label, p, "BREAKING",
                                    "added required/unique property"))
        else:
            atoms.append(ChangeAtom("prop_added_optional", label, p, "BACKWARD",
                                    "added optional property"))
    for p in sorted(set(old_props) - set(new_props)):          # removed props
        atoms.append(ChangeAtom("prop_removed", label, p, "FORWARD", "removed property"))
    for p in sorted(set(old_props) & set(new_props)):          # changed props
        o, n = old_props[p] or {}, new_props[p] or {}
        if str(o.get("type", "")).upper() != str(n.get("type", "")).upper():
            atoms.append(ChangeAtom("prop_retyped", label, p, "BREAKING", "property type changed"))
        elif not _is_strengthening_constraint(o.get("constraint", "")) and \
                _is_strengthening_constraint(n.get("constraint", "")):
            atoms.append(ChangeAtom("constraint_tightened", label, p, "BREAKING",
                                    "constraint tightened (required/unique added)"))
        elif _is_strengthening_constraint(o.get("constraint", "")) and \
                not _is_strengthening_constraint(n.get("constraint", "")):
            atoms.append(ChangeAtom("constraint_loosened", label, p, "BACKWARD",
                                    "constraint loosened"))
    return atoms


def _classify_rel(label: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[ChangeAtom]:
    atoms: List[ChangeAtom] = []
    o, n = old or {}, new or {}
    if o.get("source") != n.get("source") or o.get("target") != n.get("target"):
        atoms.append(ChangeAtom("rel_endpoint_changed", label, "", "BREAKING",
                                "domain/range (source/target) changed"))
    oc, nc = _CARD_RANK.get(str(o.get("cardinality", "")).upper()), \
        _CARD_RANK.get(str(n.get("cardinality", "")).upper())
    if oc is not None and nc is not None and nc != oc:
        if nc < oc:
            atoms.append(ChangeAtom("rel_cardinality_tightened", label, "", "BREAKING",
                                    "cardinality tightened"))
        else:
            atoms.append(ChangeAtom("rel_cardinality_loosened", label, "", "BACKWARD",
                                    "cardinality loosened"))
    return atoms


def semver_distance(old_version: str, new_version: str) -> int:
    """A coarse version-chain distance for the freshness staleness term (ia4.3):
    number of major+minor steps between two semvers (patch ignored — patches are
    compatible by construction). Falls back to 1 when either is unparseable but
    they differ, 0 when equal."""
    from .versioning import parse_semver

    a, b = parse_semver(old_version or ""), parse_semver(new_version or "")
    if a is None or b is None:
        return 0 if (old_version or "") == (new_version or "") else 1
    return abs(b[0] - a[0]) * 1000 + abs(b[1] - a[1]) if a[:2] != b[:2] else (
        0 if a == b else 1)


def classify_ontology_change(old: Any, new: Any) -> CompatibilityReport:
    """Classify every change atom between two ontologies (or their ``to_dict``)."""
    od = old.to_dict() if hasattr(old, "to_dict") else dict(old)
    nd = new.to_dict() if hasattr(new, "to_dict") else dict(new)
    atoms: List[ChangeAtom] = []

    on, nn = od.get("nodes", {}) or {}, nd.get("nodes", {}) or {}
    for k in sorted(set(nn) - set(on)):
        atoms.append(ChangeAtom("node_added", k, "", "BACKWARD", "added node label"))
    for k in sorted(set(on) - set(nn)):
        atoms.append(ChangeAtom("node_removed", k, "", "BREAKING", "removed node label"))
    for k in sorted(set(on) & set(nn)):
        atoms.extend(_classify_node(k, on[k], nn[k]))

    orl, nrl = od.get("relationships", {}) or {}, nd.get("relationships", {}) or {}
    for k in sorted(set(nrl) - set(orl)):
        atoms.append(ChangeAtom("rel_added", k, "", "BACKWARD", "added relationship type"))
    for k in sorted(set(orl) - set(nrl)):
        atoms.append(ChangeAtom("rel_removed", k, "", "BREAKING", "removed relationship type"))
    for k in sorted(set(orl) & set(nrl)):
        atoms.extend(_classify_rel(k, orl[k], nrl[k]))

    return CompatibilityReport(atoms=atoms)
