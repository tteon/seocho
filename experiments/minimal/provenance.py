"""Where a fact came from, recovered after the fact.

The extraction path records almost no provenance. A node carries `_source_id`
and `_workspace_id` and nothing else (seocho/store/graph.py:385-386). There is a
`(:Chunk)-[:MENTIONS]->(entity)` edge, but it is created by testing whether the
entity's name appears literally in the chunk text
(seocho/index/pipeline.py:375-396), its offsets are the chunk's span rather than
the mention's (seocho/index/runtime_memory.py:464-467), and nothing in the query
layer ever reads it. So there is no way to ask a served figure where it came
from.

Fixing that properly means changing extraction, which would invalidate every
snapshot already committed. This recovers the same information from what is
already stored: an extracted figure and the source passage it was extracted
from are both on disk, so the figure can be located in the passage afterwards.

Two things this is careful about.

A mis-scaled extraction has to still anchor. A model that read "$5.2 billion"
and wrote 5200 produced a number that does not occur in the text, and that
extraction is exactly the one worth catching, so the search covers the value as
parsed and the same mantissa at every scale. Exact matches win, so a correct
extraction is never dragged onto a wrongly-scaled neighbour.

An ambiguous anchor is refused, not guessed. A figure occurring in three places
gets no anchor at all. Guessing would manufacture agreement between facts that
have none, which is the failure this whole line of work exists to avoid.

Used by provenance_keying.py and by materialize_anchors.py, which writes the
recovered anchors beside the snapshots as a derived layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9,
                "trillion": 1e12}
SCALES = (1e3, 1e6, 1e9, 1e12)


@dataclass(frozen=True)
class Token:
    """A number as it appears in a passage, and what it means there."""

    passage: int
    offset: int
    end: int
    literal: str
    value: float
    scaled: float          # with a following scale word applied, if any


@dataclass(frozen=True)
class Anchor:
    passage: int
    offset: int
    literal: str
    source_value: float
    extracted_value: float
    exact: bool            # False when only a rescaled form matched

    @property
    def scale_ratio(self) -> float:
        if self.source_value == 0:
            return 0.0
        return self.extracted_value / self.source_value


def parse_amount(text: str) -> float | None:
    """The first figure in a string, with any scale word applied."""
    raw = str(text or "")
    found = _NUMBER.search(raw)
    if not found:
        return None
    try:
        value = float(found.group(0).replace(",", ""))
    except ValueError:
        return None
    lowered = raw.lower()
    for word, factor in _SCALE_WORDS.items():
        if re.search(rf"\b{word}s?\b", lowered):
            value *= factor
            break
    if "(" in raw and ")" in raw:      # accounting negative
        value = -abs(value)
    return value


def tokenize(passages: Iterable[str]) -> list[Token]:
    """Every number in every passage, with its position and its scale."""
    tokens: list[Token] = []
    for index, text in enumerate(passages):
        body = str(text or "")
        for match in _NUMBER.finditer(body):
            try:
                value = float(match.group(0).replace(",", ""))
            except ValueError:
                continue
            if value == 0:
                continue
            trailing = body[match.end():match.end() + 24].lower()
            scaled = value
            for word, factor in _SCALE_WORDS.items():
                if re.match(rf"\s*{word}s?\b", trailing):
                    scaled = value * factor
                    break
            tokens.append(Token(index, match.start(), match.end(),
                                match.group(0), value, scaled))
    return tokens


def close(a: float, b: float, tolerance: float = 0.001) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def locate(value: float, tokens: list[Token]) -> Anchor | None:
    """The one place this figure came from, or nothing.

    Exact first, then rescaled. Either round returning more than one candidate
    means the figure cannot be attributed and no anchor is produced.
    """
    exact = [t for t in tokens
             if close(value, t.value) or close(value, t.scaled)]
    if len(exact) == 1:
        token = exact[0]
        source = token.scaled if close(value, token.scaled) else token.value
        return Anchor(token.passage, token.offset, token.literal, source,
                      value, True)
    if exact:
        return None

    forms = [value * s for s in SCALES] + [value / s for s in SCALES]
    rescaled = [t for t in tokens
                if any(close(f, t.value) or close(f, t.scaled) for f in forms)]
    if len(rescaled) == 1:
        token = rescaled[0]
        source = token.scaled if any(close(f, token.scaled) for f in forms) \
            else token.value
        return Anchor(token.passage, token.offset, token.literal, source,
                      value, False)
    return None


def window(passages: list[str], anchor: Anchor, span: int = 90) -> str:
    """The text around an anchor — the evidence a reader would want shown.

    This is what a served figure should be able to display beside itself, and
    what the graph currently cannot produce for any fact.
    """
    if anchor.passage >= len(passages):
        return ""
    text = str(passages[anchor.passage] or "")
    start = max(0, anchor.offset - span)
    end = min(len(text), anchor.offset + span)
    return re.sub(r"\s+", " ", text[start:end]).strip()
