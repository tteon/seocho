"""Keet-style ontology evaluation metrics (structural / richness / logical).

hadry: everyone measures downstream answer quality and misses EXTRACTION/ONTOLOGY
quality — ontology evaluation is barely considered. These are the metrics from Keet,
*An Introduction to Ontology Engineering* §11.2.5 (Table 11.1), adapted to SEOCHO's
LPG ontology so the quality of an ontology — especially one INDUCED at cold-start —
is a first-class, computed output, not an afterthought.

Applicable to a single ontology (no source needed):
- **size** = |C| + |OP| + |DP| (classes + object properties + data properties).
- **inheritance richness** IR = avg direct subclasses per class (taxonomy shape).
- **attribute richness** AR = avg data properties per class.
- **relationship richness** RR = non-isa relations / (relations + isa) — how much of
  the schema is real relations vs bare taxonomy (Tartir et al.).
- **cohesion** = fraction of classes connected to ≥1 relation (not isolated).

Against a reference ontology (for the cold-start induced-vs-authored comparison):
- **correctness** = every type/relation in M is also in the reference (M ⊆ O).
- **completeness** = every reference type is preserved in M (O ⊆ M coverage).

Good-value bands (Table 11.1, 4-point: small 0-.25 / medium .25-.5 / moderate .5-.75
/ large .75-1) are attached per metric so a score is interpretable, not just a number.
"""

from __future__ import annotations

from typing import Any, Dict


def _classes(onto: Any) -> Dict[str, Any]:
    return getattr(onto, "nodes", {}) or {}


def _rels(onto: Any) -> Dict[str, Any]:
    return getattr(onto, "relationships", {}) or {}


def _band(x: float) -> str:
    if x <= 0.25:
        return "small"
    if x <= 0.5:
        return "medium"
    if x <= 0.75:
        return "moderate"
    return "large"


def compute_ontology_metrics(onto: Any, *, reference: Any = None) -> Dict[str, Any]:
    """Compute the Keet §11.2.5 metrics for ``onto`` (and vs ``reference`` if given)."""
    classes = _classes(onto)
    rels = _rels(onto)
    n_classes = len(classes)
    n_rels = len(rels)

    # data/object property counts
    n_data_props = sum(len(getattr(nd, "properties", {}) or {}) for nd in classes.values())

    # inheritance: count direct subclass edges via broader
    isa_edges = 0
    for nd in classes.values():
        isa_edges += len(getattr(nd, "broader", []) or [])
    inheritance_richness = round(isa_edges / n_classes, 3) if n_classes else 0.0

    attribute_richness = round(n_data_props / n_classes, 3) if n_classes else 0.0

    # relationship richness: real relations vs (relations + isa edges)
    denom = n_rels + isa_edges
    relationship_richness = round(n_rels / denom, 3) if denom else 0.0

    # cohesion proxy: fraction of classes touched by >=1 relationship
    touched = set()
    for rd in rels.values():
        s, t = getattr(rd, "source", None), getattr(rd, "target", None)
        if s:
            touched.add(str(s))
        if t:
            touched.add(str(t))
    cohesion = round(len(touched & set(classes)) / n_classes, 3) if n_classes else 0.0

    size = n_classes + n_rels + n_data_props

    out: Dict[str, Any] = {
        "size": size,
        "classes": n_classes,
        "relationships": n_rels,
        "data_properties": n_data_props,
        "inheritance_richness": inheritance_richness,
        "attribute_richness": attribute_richness,
        "relationship_richness": relationship_richness,
        "cohesion": cohesion,
        "bands": {
            "relationship_richness": _band(relationship_richness),
            "cohesion": _band(cohesion),
        },
    }

    if reference is not None:
        ref_c, ref_r = set(_classes(reference)), set(_rels(reference))
        m_c, m_r = set(classes), set(rels)
        ref_all = ref_c | ref_r
        m_all = m_c | m_r
        # correctness: M's vocabulary is a subset of the reference's
        extra = sorted(m_all - ref_all)
        correctness_ratio = round(len(m_all & ref_all) / len(m_all), 3) if m_all else 1.0
        # completeness: how much of the reference M preserves
        missing = sorted(ref_all - m_all)
        completeness_ratio = round(len(ref_all & m_all) / len(ref_all), 3) if ref_all else 1.0
        out["vs_reference"] = {
            "correctness": correctness_ratio,          # 1.0 = M adds nothing outside O
            "correct": len(extra) == 0,
            "completeness": completeness_ratio,         # 1.0 = M preserves all of O
            "complete": len(missing) == 0,
            "extra_not_in_reference": extra[:20],
            "missing_from_reference": missing[:20],
        }
    return out
