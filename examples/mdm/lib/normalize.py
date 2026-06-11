"""Name + numeric normalization for the MDM demo ($0, deterministic, no LLM).

Two jobs:

1. **Entity-name normalization** — turn LLM-extracted company-name variants
   ("Microsoft Corporation" / "Microsoft Corp." / "The Microsoft Co") into a
   comparable key, and decide name-level identity with the high-precision
   ordered-token-prefix rule from ``examples/contextgraph/merge_entities.py``.
2. **Numeric normalization** — parse model-extracted financial figures kept as
   strings per the graph property discipline ("$242.3B", "$242,290 million",
   "(1,234) thousand") into base-unit floats so survivorship can vote on them.
   "$242.3B" and "$242,290 million" must count as AGREEMENT (rounding), which
   is why equivalence is a relative tolerance, not string equality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# ---------------------------------------------------------------------------
# Entity names
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9 ]")

# Trailing legal-form tokens that differ across models for the same entity.
# Stripped from the END only, and never down to an empty name.
_CORP_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co",
    "ltd", "limited", "plc", "llc", "lp", "holdings", "group",
}


def norm_tokens(name: str, *, strip_corp: bool = True) -> List[str]:
    """Lowercase, strip punctuation, drop leading article + trailing legal forms."""
    toks = _PUNCT_RE.sub(" ", (name or "").lower()).split()
    if toks and toks[0] == "the":
        toks = toks[1:]
    if strip_corp:
        while len(toks) > 1 and toks[-1] in _CORP_SUFFIXES:
            toks = toks[:-1]
    return toks


def norm_key(name: str) -> str:
    """Blocking key: same key ⇒ exact_key match candidates."""
    return " ".join(norm_tokens(name))


def is_token_prefix(a: List[str], b: List[str]) -> bool:
    """True if token-list ``a`` is an ordered prefix of ``b``.

    High precision (merge_entities.py rule): 'jacob' ⊑ 'jacob palme', but
    'alan' is NOT a prefix of 'friend of alan'.
    """
    return 0 < len(a) <= len(b) and b[: len(a)] == a


def names_match(a: str, b: str) -> bool:
    """Name-level identity: one normalized token sequence prefixes the other."""
    ta, tb = norm_tokens(a), norm_tokens(b)
    return bool(ta and tb and (is_token_prefix(ta, tb) or is_token_prefix(tb, ta)))


# ---------------------------------------------------------------------------
# Numeric values
# ---------------------------------------------------------------------------

_SCALES = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mm": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}

_MONEY_RE = re.compile(
    r"""(?P<paren>\()?\s*
        (?P<minus>-)?\s*
        (?P<cur>[$€£¥₩])?\s*
        (?P<num>\d[\d,]*(?:\.\d+)?)
        \s*\)?\s*(?P<scale>trillion|billion|million|thousand|tn|bn|mm|[tbmk])?\b
        \s*(?P<pct>%)?""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedValue:
    """A model-extracted figure normalized to base units."""

    value: float            # base units (e.g. 242_290_000_000.0)
    currency: str           # "$" etc., or "" when none was written
    is_pct: bool
    sig_digits: int         # significant digits AS WRITTEN — the least-rounded
                            # source wins survivorship ties
    raw: str                # figure exactly as extracted (provenance)


def _sig_digits(num_str: str) -> int:
    digits = num_str.replace(",", "").replace(".", "").lstrip("0")
    return max(len(digits), 1)


def parse_value(raw: object) -> Optional[ParsedValue]:
    """Parse the first money/number expression in ``raw``; None if absent."""
    text = str(raw or "").strip()
    if not text:
        return None
    m = _MONEY_RE.search(text)
    if not m:
        return None
    value = float(m.group("num").replace(",", ""))
    scale = (m.group("scale") or "").lower()
    if scale:
        value *= _SCALES[scale]
    if m.group("paren") or m.group("minus"):
        value = -value
    return ParsedValue(
        value=value,
        currency=m.group("cur") or "",
        is_pct=bool(m.group("pct")),
        sig_digits=_sig_digits(m.group("num")),
        raw=text,
    )


def values_agree(a: ParsedValue, b: ParsedValue, *, rel_tol: float = 0.005) -> bool:
    """Relative-tolerance equivalence: rounding ("242.3B" vs "242,290M") agrees,
    a real discrepancy ("242.3B" vs "249B") does not. Percent never equals
    non-percent even when the numbers coincide."""
    if a.is_pct != b.is_pct:
        return False
    if a.value == b.value:
        return True
    denom = max(abs(a.value), abs(b.value))
    if denom == 0.0:
        return True
    return abs(a.value - b.value) <= rel_tol * denom
