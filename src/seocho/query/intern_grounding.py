"""Ground text2cypher in the shared intern table + competency questions (seocho-ia4).

The Cypher-generation agent's hardest moment: it reads a user request and cannot find
the entity — it doesn't know whether "Apple" is a real node, what its canonical id is,
or which query shape the question wants. Left alone it hallucinates a name/label or
gives up. This is exactly where the shared canonical namespace (SharedInternTable) and
the ontology's competency questions earn their keep:

1. **Mention resolution** — resolve surface mentions in the request against the shared
   intern table (the canonical entity address space). A mention that resolves gives the
   Cypher a REAL canonical id to bind (grounded, not guessed). A mention that does NOT
   resolve is surfaced explicitly (``unresolved``) — the agent's "can't find entity"
   case becomes a routable signal (fuzzy/vector fallback), not a silent wrong Cypher.
2. **Intent via competency questions** — rank the request against the ontology's
   competency questions (tf-idf cosine baseline; swap in embeddings for semantic) to
   pick the closest CQ, whose known query shape guides generation.

Pure-Python, dependency-free (tf-idf is a lightweight baseline; the design note flags
bge/semantic as the richer option). Read-only; produces a grounding the Cypher prompt
consumes.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..index.identity import compute_node_identity

# Capitalized multi-word spans (Acme, Apple Inc., Bank of America) — a cheap mention
# heuristic; real NER can replace it without changing the resolution contract.
_MENTION_RE = re.compile(r"\b([A-Z][\w&.\-]*(?:\s+(?:of\s+|the\s+)?[A-Z][\w&.\-]*)*)")
_WORD_RE = re.compile(r"[a-z0-9]+")
_CAPWORD_RE = re.compile(r"[A-Z][\w&.\-]*")
# sentence-initial / question words that a greedy capitalized-span match sweeps up
# but are never entities — excluded from the can't-find signal.
_STOP = {"what", "which", "who", "whom", "whose", "when", "where", "why", "how",
         "compare", "list", "show", "find", "give", "did", "does", "is", "are",
         "the", "a", "an", "and", "or", "of", "for", "in", "on", "to"}


@dataclass
class Grounding:
    resolved: List[Tuple[str, str]] = field(default_factory=list)     # (mention, canonical_id)
    unresolved: List[str] = field(default_factory=list)               # can't-find-entity signal
    intents: List[Tuple[str, float]] = field(default_factory=list)    # (competency question, score)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved": [{"mention": m, "canonical_id": c} for m, c in self.resolved],
            "unresolved": list(self.unresolved),
            "intents": [{"question": q, "score": round(s, 3)} for q, s in self.intents],
            "resolution_rate": round(len(self.resolved) / max(len(self.resolved) + len(self.unresolved), 1), 3),
        }


def extract_mentions(request: str) -> List[str]:
    seen, out = set(), []
    for m in _MENTION_RE.findall(request or ""):
        m = m.strip(" .")
        if m and m.lower() not in seen and len(m) > 1:
            seen.add(m.lower())
            out.append(m)
    return out


def resolve_mentions(
    request: str,
    *,
    intern_table: Any,
    ontology: Any,
    workspace_id: str = "default",
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Resolve each request mention against the intern table. Returns
    (resolved [(mention, canonical_id)], unresolved [mention])."""
    node_defs = getattr(ontology, "nodes", {}) or {}
    # single-identity-key labels: a lone mention can fill the one key (usually name)
    single_key_labels = [
        (label, list(getattr(nd, "identity_keys", []) or [])[0])
        for label, nd in node_defs.items()
        if len(getattr(nd, "identity_keys", []) or []) == 1
    ]
    def _lookup(text: str) -> Optional[str]:
        for label, key in single_key_labels:
            identity = compute_node_identity(label, {key: text}, [key])
            if identity:
                canon = intern_table.get(workspace_id, identity)
                if canon:
                    return canon
        return None

    resolved: List[Tuple[str, str]] = []
    unresolved: List[str] = []
    seen_unres: set = set()
    for mention in extract_mentions(request):
        # try the full span, then each capitalized token inside it (a greedy span
        # like "Compare Apple" should still resolve "Apple").
        candidates = [mention] + [w for w in _CAPWORD_RE.findall(mention) if w != mention]
        hit_text = hit = None
        for cand in candidates:
            canon = _lookup(cand)
            if canon:
                hit_text, hit = cand, canon
                break
        if hit:
            resolved.append((hit_text, hit))
        else:
            # report only genuine, non-stopword mentions as the can't-find signal
            for w in _CAPWORD_RE.findall(mention) or [mention]:
                if w.lower() not in _STOP and w.lower() not in seen_unres:
                    seen_unres.add(w.lower())
                    unresolved.append(w)
    return resolved, unresolved


def _tfidf_vectors(docs: List[str]) -> List[Dict[str, float]]:
    tokenized = [_WORD_RE.findall(d.lower()) for d in docs]
    df: Counter = Counter()
    for toks in tokenized:
        for w in set(toks):
            df[w] += 1
    n = len(docs)
    vecs = []
    for toks in tokenized:
        tf = Counter(toks)
        total = max(len(toks), 1)
        vec = {}
        for w, c in tf.items():
            idf = math.log((n + 1) / (df[w] + 1)) + 1.0
            vec[w] = (c / total) * idf
        vecs.append(vec)
    return vecs


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[w] * b.get(w, 0.0) for w in a)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def rank_competency_questions(
    request: str, competency_questions: List[str], *, top_k: int = 3,
) -> List[Tuple[str, float]]:
    """Rank CQs by tf-idf cosine similarity to the request (intent hint)."""
    if not competency_questions:
        return []
    vecs = _tfidf_vectors([request] + list(competency_questions))
    req, cqs = vecs[0], vecs[1:]
    scored = [(competency_questions[i], _cosine(req, cqs[i])) for i in range(len(cqs))]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(q, s) for q, s in scored[:top_k] if s > 0]


def ground_request(
    request: str,
    *,
    intern_table: Any,
    ontology: Any,
    workspace_id: str = "default",
    competency_questions: Optional[List[str]] = None,
    top_k: int = 3,
) -> Grounding:
    """Full grounding: resolved canonical entities + unresolved (can't-find signal) +
    top competency-question intents — the context a Cypher-gen prompt should consume."""
    resolved, unresolved = resolve_mentions(
        request, intern_table=intern_table, ontology=ontology, workspace_id=workspace_id)
    intents = rank_competency_questions(request, competency_questions or [], top_k=top_k)
    return Grounding(resolved=resolved, unresolved=unresolved, intents=intents)
