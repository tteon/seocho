"""Scored ontology grounding — NL intent → ranked ontology types (icml port).

Ports the icml2026 `fibo_ground_node_label` / `fibo_ground_edge_type`
pattern: given a natural-language intent term, score it against the
ontology's relationship types / node labels and return ranked
``(type, score)`` above a threshold (e.g. `audit committee` →
`[("hasCommittee", 0.62), ("HAS_COMMITTEE", 0.55), ("OVERSEES", 0.41)]`).

The original used embedding cosine. SEOCHO has no live embedding backend
(the OpenAI key is invalid; MARA serves no embeddings), so the default
scorer is a deterministic **lexical** similarity (camelCase/snake-aware
tokenization + content-token weighted Jaccard with a substring bonus).
The scorer is pluggable via ``scorer=`` so an embedding-backed scorer can
drop in later with no caller change — the contract (ranked, threshold-
gated grounding) is what we port.

Motivated by the F8 finding: most FinDER queries return 0 records because
the heuristic relationship/label match (exact/alias only) misses
semantically-equivalent ontology types. Scored grounding bridges
"manages" → "LED_BY" without an exact alias.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Structural noise tokens in type names — carry no grounding signal.
_TYPE_STOPWORDS = {
    "has", "have", "is", "are", "of", "by", "to", "the", "a", "an", "in",
    "on", "with", "for", "and", "or", "rel", "relationship", "type", "node",
}

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def tokenize_type_name(name: str) -> set:
    """Split a type name into content tokens.

    Handles camelCase (`hasCommittee`), snake/SCREAMING_CASE
    (`HAS_COMMITTEE`), and plain phrases (`audit committee`). Lowercases,
    drops structural stopwords. Returns a set of content tokens.
    """
    if not name:
        return set()
    spaced = _CAMEL_RE.sub(" ", str(name))
    spaced = _NONWORD_RE.sub(" ", spaced.lower())
    return {t for t in spaced.split() if t and t not in _TYPE_STOPWORDS}


def lexical_similarity(intent: str, candidate: str) -> float:
    """Deterministic similarity in [0, 1].

    Weighted Jaccard over content tokens plus a containment bonus when one
    token set is a subset of the other (e.g. {committee} ⊂ {audit,
    committee}). Empty token sets score 0.
    """
    a = tokenize_type_name(intent)
    b = tokenize_type_name(candidate)
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    union = a | b
    jaccard = len(inter) / len(union)
    # Containment bonus: reward when all of the smaller set is matched.
    smaller = min(len(a), len(b))
    containment = len(inter) / smaller if smaller else 0.0
    score = 0.6 * jaccard + 0.4 * containment
    return round(min(1.0, score), 4)


Scorer = Callable[[str, str], float]


def ground(
    intent: str,
    candidates: Sequence[str],
    *,
    top_k: int = 3,
    threshold: float = 0.4,
    scorer: Optional[Scorer] = None,
) -> List[Tuple[str, float]]:
    """Score ``intent`` against ``candidates``; return top_k above threshold.

    Sorted by descending score, then candidate name for determinism (so
    ties don't flake). Mirrors fibo_ground's (type, score) output shape.
    """
    score_fn = scorer or lexical_similarity
    scored = [(c, score_fn(intent, c)) for c in candidates]
    scored = [(c, s) for c, s in scored if s >= threshold]
    scored.sort(key=lambda cs: (-cs[1], cs[0]))
    return scored[:top_k]


def _relationship_candidates(ontology) -> Dict[str, str]:
    """Map each relationship grounding-string → its canonical type name.

    Includes the type name itself plus aliases / same_as so grounding can
    match on any surface form but always returns the canonical type.
    """
    out: Dict[str, str] = {}
    for name, rel_def in getattr(ontology, "relationships", {}).items():
        out[name] = name
        for alias in getattr(rel_def, "aliases", ()) or ():
            out[str(alias)] = name
        same_as = getattr(rel_def, "same_as", "") or ""
        if same_as:
            out[str(same_as)] = name
    return out


def ground_edge_type(
    intent: str,
    ontology,
    *,
    top_k: int = 3,
    threshold: float = 0.4,
    scorer: Optional[Scorer] = None,
) -> List[Tuple[str, float]]:
    """Ground an NL intent to ontology relationship types (canonical names).

    Scores every surface form (name + aliases + same_as), then collapses to
    the best score per canonical relationship.
    """
    surface_to_canon = _relationship_candidates(ontology)
    ranked = ground(intent, list(surface_to_canon.keys()), top_k=top_k * 3, threshold=threshold, scorer=scorer)
    best: Dict[str, float] = {}
    for surface, score in ranked:
        canon = surface_to_canon[surface]
        if score > best.get(canon, 0.0):
            best[canon] = score
    collapsed = sorted(best.items(), key=lambda cs: (-cs[1], cs[0]))
    return collapsed[:top_k]


def ground_node_label(
    intent: str,
    ontology,
    *,
    top_k: int = 3,
    threshold: float = 0.4,
    scorer: Optional[Scorer] = None,
) -> List[Tuple[str, float]]:
    """Ground an NL intent to ontology node labels."""
    labels = list(getattr(ontology, "nodes", {}).keys())
    return ground(intent, labels, top_k=top_k, threshold=threshold, scorer=scorer)
