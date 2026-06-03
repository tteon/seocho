"""AnswerShape — classify the expected answer shape and steer terse synthesis.

Empirically motivated (opik icml/kdd traces + the FinDER T2 baseline): the
query lane retrieves the right facts (contains-match = 1.0) but the
synthesizer wraps them in prose ("Based on the query results, ... is **X**"),
so exact-match = 0 and token-F1 ≈ 0.18. The kdd `build_semantic_intent_context`
classifies an `answer_shape` (e.g. ``scalar_metric``) *before* synthesis and
emits a terse answer. This module is seocho's minimal port of that idea.

Two-tier classification per the agreed design: rule-based first (cheap,
deterministic), with room for an LLM fallback when rules are uncertain
(not wired in this minimal cut — rules cover the FinDER bucket).
"""

from __future__ import annotations

import os
import re
from enum import Enum
from typing import Optional


def answer_shape_enabled() -> bool:
    """AnswerShape terse-synthesis steering — DEFAULT ON (opt-out).

    Adopted 2026-06-03 after the wide FinDER validation: token_f1 0.146→0.629
    and exact 0→0.60 across all reasoning buckets (single_hop 0.15→0.61,
    numeric 0.26→1.0, compositional 0.075→0.50), with zero regression on the
    two unknown-shape cases (no directive emitted → baseline prose). Explanation
    /unknown shapes are a provable no-op (terse_directive returns None), so
    default-on cannot silently regress prose answers (CLAUDE.md §20). Disable
    per call/run with SEOCHO_ANSWER_SHAPE=0. Mirrors
    _verified_financial_answer_enabled's opt-out shape.
    """
    return str(os.environ.get("SEOCHO_ANSWER_SHAPE", "1")).strip().lower() not in ("0", "false", "no")


class AnswerShape(str, Enum):
    SCALAR_METRIC = "scalar_metric"   # "how much / what was revenue" → a number/amount
    ENTITY_NAME = "entity_name"       # "who is / which company" → a name
    ENTITY_LIST = "entity_list"       # "list / which ... (plural)" → a short list
    LOCATION = "location"             # "where is X" → a place
    EXPLANATION = "explanation"       # "why / how does" → prose (no terseness)
    UNKNOWN = "unknown"


# Rule signals. Order matters: the first matching shape wins.
_SCALAR_RE = re.compile(
    r"\b(how much|how many|what (?:was|is|were) (?:the )?(?:total |net |gross )?"
    r"(revenue|income|profit|loss|amount|value|dividend|sales|margin|expense|cost|"
    r"settlement|cash|eps|earnings|fee|price))\b",
    re.IGNORECASE,
)
_NUMERIC_HINT_RE = re.compile(r"\b(revenue|dividend|settlement|amount|value|total|percentage|%)\b", re.IGNORECASE)
_LOCATION_RE = re.compile(r"\bwhere\b|\bheadquarter", re.IGNORECASE)
_WHO_RE = re.compile(r"\b(who is|who are|name the|which (?:person|executive|chair|ceo|cfo|officer))\b", re.IGNORECASE)
_LIST_RE = re.compile(r"\b(list|which .*\b(s)\b|what are|name all)\b", re.IGNORECASE)
_EXPLAIN_RE = re.compile(r"\b(why|how does|explain|describe|what (?:is the )?impact)\b", re.IGNORECASE)


def classify_answer_shape(question: str) -> AnswerShape:
    """Rule-based answer-shape classification for the question.

    Deterministic and dependency-free. Returns UNKNOWN when no rule fires
    (the caller then leaves synthesis untouched — baseline behavior).
    """
    q = (question or "").strip()
    if not q:
        return AnswerShape.UNKNOWN
    if _LOCATION_RE.search(q):
        return AnswerShape.LOCATION
    if _WHO_RE.search(q):
        return AnswerShape.ENTITY_NAME
    if _SCALAR_RE.search(q) or (_NUMERIC_HINT_RE.search(q) and _starts_amount(q)):
        return AnswerShape.SCALAR_METRIC
    if _EXPLAIN_RE.search(q):
        return AnswerShape.EXPLANATION
    if _LIST_RE.search(q):
        return AnswerShape.ENTITY_LIST
    return AnswerShape.UNKNOWN


def _starts_amount(q: str) -> bool:
    return bool(re.match(r"\s*(what|how much|how many)\b", q, re.IGNORECASE))


# Per-shape synthesis directive appended to the answer prompt. EXPLANATION /
# UNKNOWN intentionally have no directive (prose is correct there).
_TERSE_DIRECTIVES = {
    AnswerShape.SCALAR_METRIC: (
        "Answer with ONLY the value (the number/amount with its unit), e.g. "
        "'$211.9 billion'. No preamble, no 'Based on the query results', no "
        "sentence — just the value."
    ),
    AnswerShape.ENTITY_NAME: (
        "Answer with ONLY the name, e.g. 'John L. Hennessy'. No preamble, no "
        "sentence — just the name."
    ),
    AnswerShape.LOCATION: (
        "Answer with ONLY the place, e.g. 'Cupertino, California'. No preamble, "
        "no sentence — just the location."
    ),
    AnswerShape.ENTITY_LIST: (
        "Answer with ONLY a comma-separated list of the names. No preamble, no "
        "sentences."
    ),
}


def terse_directive(shape: AnswerShape) -> Optional[str]:
    """Return the synthesis directive for a shape, or None to leave the
    answer prompt unchanged (EXPLANATION / UNKNOWN)."""
    return _TERSE_DIRECTIVES.get(shape)
