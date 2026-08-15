"""
Ontology slice extraction — relevance-pruned subset for a given query intent.

Closes seocho-cvys. ``compile_ontology_context`` emits the full ontology
vocabulary regardless of intent. For a 5-class teaching ontology this is
fine; for a real ontology of 1000+ classes (full FIBO is ~3500 classes),
every call ships unnecessary context — increased token cost, worse
KV-cache hit ratio.

The slicer uses a conservative substring match against label and
relationship names, walks one level of neighbours so relationships
between matched classes survive, and falls back to the full ontology
when the intent is genuinely ambiguous (zero matches).

This is a heuristic — production deployments wanting better recall
should swap in an embedding-based intent classifier. The interface
(``slice_ontology(ontology, intent)``) stays the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class OntologySlice:
    """Subset of an ontology relevant to a given intent."""

    matched_labels: Set[str] = field(default_factory=set)
    related_labels: Set[str] = field(default_factory=set)
    matched_relationships: Set[str] = field(default_factory=set)
    intent_terms: List[str] = field(default_factory=list)
    fallback_to_full: bool = False

    @property
    def all_labels(self) -> Set[str]:
        return self.matched_labels | self.related_labels

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched_labels": sorted(self.matched_labels),
            "related_labels": sorted(self.related_labels),
            "matched_relationships": sorted(self.matched_relationships),
            "intent_terms": list(self.intent_terms),
            "fallback_to_full": self.fallback_to_full,
            "label_count": len(self.all_labels),
            "relationship_count": len(self.matched_relationships),
        }


def _tokenize_intent(intent: str) -> List[str]:
    """Lowercase tokens; strip punctuation. ≥3 chars to skip stop words."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]+", intent.lower())
    return [t for t in tokens if len(t) >= 3]


def _label_matches_term(label: str, term: str) -> bool:
    """Match label against term — case-insensitive substring or PascalCase split."""
    label_lower = label.lower()
    if term in label_lower:
        return True
    # Split PascalCase: 'LegalPerson' → ['legal', 'person']
    parts = re.findall(r"[A-Z][a-z]*", label)
    if any(term == p.lower() for p in parts):
        return True
    return False


def slice_ontology(
    ontology: Any,
    intent: str,
    *,
    expand_neighbours: bool = True,
) -> OntologySlice:
    """Compute the slice of *ontology* relevant to *intent*.

    Algorithm:

    1. Tokenize the intent → keyword set.
    2. Match each token against ontology label names (substring +
       PascalCase split) and relationship type names.
    3. If ``expand_neighbours``: walk the ontology graph one step from
       each matched label, adding source/target endpoints of any
       relationship that touches a matched label.
    4. If zero matches surface, set ``fallback_to_full=True`` and
       return an empty slice — caller is expected to fall back to the
       full ontology context. (Conservative: better to over-ship than
       silently strip a relevant class.)

    Parameters
    ----------
    ontology:
        seocho.Ontology instance.
    intent:
        Free-text query/question.
    expand_neighbours:
        Whether to include endpoints of relationships touching matched
        labels. Defaults to True.
    """
    sl = OntologySlice()
    tokens = _tokenize_intent(intent)
    sl.intent_terms = list(tokens)
    if not tokens:
        sl.fallback_to_full = True
        return sl

    nodes = getattr(ontology, "nodes", {}) or {}
    rels = getattr(ontology, "relationships", {}) or {}

    # 1. Direct label matches
    for label in nodes.keys():
        for term in tokens:
            if _label_matches_term(label, term):
                sl.matched_labels.add(label)
                break

    # 2. Direct relationship matches
    for rtype in rels.keys():
        for term in tokens:
            if _label_matches_term(rtype, term):
                sl.matched_relationships.add(rtype)
                break

    # 3. Neighbour expansion
    if expand_neighbours and sl.matched_labels:
        for rtype, rel in rels.items():
            src = getattr(rel, "source", None)
            tgt = getattr(rel, "target", None)
            if not (src and tgt):
                continue
            if src in sl.matched_labels or tgt in sl.matched_labels:
                sl.matched_relationships.add(rtype)
                if src not in sl.matched_labels:
                    sl.related_labels.add(src)
                if tgt not in sl.matched_labels:
                    sl.related_labels.add(tgt)

    # 4. Zero-match fallback
    if not sl.matched_labels and not sl.matched_relationships:
        sl.fallback_to_full = True

    return sl


def render_slice_extraction_context(
    ontology: Any,
    sl: OntologySlice,
) -> Dict[str, str]:
    """Emit an extraction-context-shaped dict for a slice.

    Mirrors the output of ``ontology.to_extraction_context()`` but
    restricted to the labels + relationships in the slice. When
    ``sl.fallback_to_full`` is True, returns the full extraction
    context — caller doesn't need to special-case.
    """
    if sl.fallback_to_full:
        return dict(ontology.to_extraction_context())

    nodes = getattr(ontology, "nodes", {}) or {}
    rels = getattr(ontology, "relationships", {}) or {}
    keep_labels = sl.all_labels
    keep_rels = sl.matched_relationships

    entity_lines = []
    for label in sorted(keep_labels):
        nd = nodes.get(label)
        if nd is None:
            continue
        desc = getattr(nd, "description", "") or ""
        entity_lines.append(f"- {label}: {desc}".rstrip())
    rel_lines = []
    for rtype in sorted(keep_rels):
        rd = rels.get(rtype)
        if rd is None:
            continue
        src = getattr(rd, "source", "?")
        tgt = getattr(rd, "target", "?")
        rel_lines.append(f"- ({src}) -[:{rtype}]-> ({tgt})")
    return {
        "ontology_name": getattr(ontology, "name", "ontology"),
        "entity_types": "\n".join(entity_lines),
        "relationship_types": "\n".join(rel_lines),
        "constraints_summary": "(slice — constraints elided; query the full ontology if needed)",
    }
