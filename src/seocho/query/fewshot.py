"""Masked-alignment few-shot retrieval (ADR-0103, slice S10).

Adapts Text2SQL-Flow's masked-alignment idea to Cypher: index (question, cypher)
examples by a MASKED skeleton (labels→[LABEL], rel types→[REL], property keys→
[PROP], literals→[VALUE]; entity/metric/period surfaces→[ENTITY]/[METRIC]/
[PERIOD]) so retrieval keys on query STRUCTURE, not surface tokens. A question
about "Microsoft's operating income trend" then retrieves an Apple-revenue delta
example because the skeleton matches.

Embeddings are pluggable (`embed_fn`, default bge via make_fastembed_backend) —
no OpenAI. The masking functions are pure and unit-tested; retrieval is a small
in-memory cosine k-NN (the NLCypherExampleStore replacement is a follow-up).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

# ---- masking (pure) ---------------------------------------------------------

_STRING_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_PARAM_RE = re.compile(r"\$\w+")
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_REL_RE = re.compile(r"\[\s*\w*\s*:\s*([A-Za-z_]\w*)\s*([^\]]*)\]")
_LABEL_RE = re.compile(r"(?<=:)[A-Za-z_]\w*")          # :Label / :REL leftovers
_PROPKEY_DOT_RE = re.compile(r"\.\s*([A-Za-z_]\w*)")    # x.prop
_PROPKEY_MAP_RE = re.compile(r"([A-Za-z_]\w*)\s*:")     # {key: ...}


def mask_cypher(cypher: str) -> str:
    """Replace labels/rel-types/property-keys/literals with structural tokens."""
    s = cypher
    s = _STRING_RE.sub("[VALUE]", s)
    s = _PARAM_RE.sub("[VALUE]", s)
    # relationship types inside [...] → [REL]
    s = _REL_RE.sub(r"[[REL]\2]", s)
    # remaining `:Label` → :[LABEL]
    s = _LABEL_RE.sub("[LABEL]", s)
    # property keys: x.prop and {key:
    s = _PROPKEY_DOT_RE.sub(".[PROP]", s)
    s = _PROPKEY_MAP_RE.sub("[PROP]:", s)
    s = _NUMBER_RE.sub("[VALUE]", s)
    return re.sub(r"\s+", " ", s).strip()


def mask_question(question: str, *, entity: str = "", metric: str = "",
                  period: str = "") -> str:
    """Replace the entity/metric/period surface spans with structural tokens."""
    s = question
    for surface, token in ((entity, "[ENTITY]"), (metric, "[METRIC]"), (period, "[PERIOD]")):
        if surface:
            s = re.sub(re.escape(surface), token, s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()


# ---- few-shot index ---------------------------------------------------------

@dataclass(slots=True)
class FewShotExample:
    question: str
    cypher: str
    masked_question: str
    masked_cypher: str
    slots: Any = None
    embedding: Optional[List[float]] = None
    metadata: dict = field(default_factory=dict)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


class FewShotIndex:
    """In-memory masked-alignment example index with bge cosine k-NN."""

    def __init__(self, embed_fn: Optional[Callable[[Sequence[str]], List[List[float]]]] = None):
        if embed_fn is None:
            from ..store.fastembed_backend import make_fastembed_backend
            be = make_fastembed_backend()
            embed_fn = be.embed if be is not None else None
        self._embed = embed_fn
        self._examples: List[FewShotExample] = []

    @property
    def examples(self) -> List[FewShotExample]:
        return list(self._examples)

    def add(self, *, question: str, cypher: str, entity: str = "", metric: str = "",
            period: str = "", slots: Any = None, metadata: Optional[dict] = None) -> FewShotExample:
        mq = mask_question(question, entity=entity, metric=metric, period=period)
        mc = mask_cypher(cypher)
        emb = self._embed([f"{mq} || {mc}"])[0] if self._embed else None
        ex = FewShotExample(question=question, cypher=cypher, masked_question=mq,
                            masked_cypher=mc, slots=slots, embedding=emb,
                            metadata=dict(metadata or {}))
        self._examples.append(ex)
        return ex

    def search(self, question: str, *, entity: str = "", metric: str = "",
               period: str = "", k: int = 4) -> List[Tuple[FewShotExample, float]]:
        """Return up to k examples ranked by masked-skeleton similarity.

        With an embed backend: cosine over the masked (question||cypher)
        embedding. Without one: lexical fallback (shared-token overlap of the
        masked question) so retrieval still works CI-safe.
        """
        if not self._examples:
            return []
        mq = mask_question(question, entity=entity, metric=metric, period=period)
        if self._embed:
            qv = self._embed([f"{mq} || "])[0]
            scored = [(ex, _cosine(qv, ex.embedding)) for ex in self._examples
                      if ex.embedding is not None]
        else:
            qtok = set(mq.lower().split())
            scored = [(ex, _overlap(qtok, set(ex.masked_question.lower().split())))
                      for ex in self._examples]
        scored.sort(key=lambda es: (-es[1], es[0].question))
        return scored[:k]


def _overlap(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0
