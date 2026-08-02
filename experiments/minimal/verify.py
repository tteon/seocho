"""Deterministic cross-view verification. No model is consulted.

This is the step the paper argues is where federation earns its keep, so its
rules are stated here in full rather than buried in a service. Two decisions
decide every number it produces, and both are recorded in a run's decisive
config:

  key rule          when two views are talking about the same fact
  value rule        when two values for that fact count as the same claim

Measured on the frozen corpus with exact key matching: 8.0% of facts are
comparable at all, and 23.5% of the comparable ones disagree, two thirds of
those by a factor of a thousand or a million.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

SCALES = (("trillion", 1e12), ("billion", 1e9), ("million", 1e6),
          ("thousand", 1e3), ("bn", 1e9), ("mm", 1e6), ("m", 1e6), ("k", 1e3))
INVALID = {"", "n/a", "na", "none", "not reported", "unknown", "null", "-"}


@dataclass(frozen=True)
class Value:
    raw: str
    canonical: str
    number: float | None

    @property
    def parsed(self) -> bool:
        return self.number is not None


@dataclass(frozen=True)
class Conflict:
    key: str
    left_view: str
    right_view: str
    left: Value
    right: Value
    kind: str

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "kind": self.kind,
                self.left_view: self.left.raw, self.right_view: self.right.raw,
                "left_number": self.left.number, "right_number": self.right.number}


def parse_value(raw: str) -> Value:
    """Formatting is not disagreement; scale is.

    "$66,669" and "66669" are the same claim. "$59.4" and "$59.4 million" are
    not, and that distinction is only visible if the scale word is applied to
    the number rather than dropped with the punctuation.
    """
    text = str(raw).strip().lower()
    if text in INVALID:
        return Value(raw=str(raw), canonical="", number=None)
    canonical = re.sub(r"[\s,]", "", text)
    scale = 1.0
    for word, multiplier in SCALES:
        if canonical.endswith(word):
            scale = multiplier
            canonical = canonical[: -len(word)]
            break
    body = re.sub(r"[^0-9.\-]", "", canonical)
    if body in ("", "-", ".", "-."):
        return Value(raw=str(raw), canonical=canonical, number=None)
    try:
        return Value(raw=str(raw), canonical=canonical, number=float(body) * scale)
    except ValueError:
        return Value(raw=str(raw), canonical=canonical, number=None)


def same_claim(left: Value, right: Value, tolerance: float = 1e-6) -> bool:
    if left.parsed and right.parsed:
        scale = max(abs(left.number), abs(right.number), 1.0)
        return abs(left.number - right.number) / scale < tolerance
    return left.canonical == right.canonical and left.canonical != ""


def classify(left: Value, right: Value) -> str:
    """Name the disagreement, because a bare rate is not actionable."""
    if not (left.parsed and right.parsed):
        return "unparseable_on_one_side"
    a, b = left.number, right.number
    if a == 0 or b == 0:
        return "zero_versus_nonzero"
    if (a < 0) != (b < 0):
        return "sign_flip"
    ratio = max(abs(a), abs(b)) / min(abs(a), abs(b))
    for power, name in ((1e9, "scale_1e9"), (1e6, "scale_1e6"), (1e3, "scale_1e3")):
        if abs(ratio - power) / power < 0.01:
            return name
    return "rounding_within_5pct" if ratio < 1.05 else "different_value"


def compare(views: dict[str, Sequence], key_of, value_of) -> dict[str, object]:
    """Compare facts across views and report both ceilings.

    Returns the comparable-key rate first, because it bounds what verification
    can see no matter how the values behave.
    """
    by_key: dict[str, dict[str, Value]] = {}
    for view, facts in views.items():
        for fact in facts:
            key = key_of(fact)
            if not key:
                continue
            by_key.setdefault(key, {}).setdefault(view, parse_value(value_of(fact)))

    comparable = {k: v for k, v in by_key.items() if len(v) >= 2}
    conflicts: list[Conflict] = []
    pairs = agree = 0
    kinds: dict[str, int] = {}
    for key, per_view in comparable.items():
        names = sorted(per_view)
        for i, left_view in enumerate(names):
            for right_view in names[i + 1:]:
                left, right = per_view[left_view], per_view[right_view]
                pairs += 1
                if same_claim(left, right):
                    agree += 1
                    continue
                kind = classify(left, right)
                kinds[kind] = kinds.get(kind, 0) + 1
                conflicts.append(Conflict(key, left_view, right_view, left, right, kind))

    total = len(by_key)
    return {
        "distinct_keys": total,
        "comparable_keys": len(comparable),
        "comparable_key_rate": round(len(comparable) / total, 6) if total else 0.0,
        "pairs": pairs,
        "agree": agree,
        "disagree": len(conflicts),
        "disagreement_rate": round(len(conflicts) / pairs, 6) if pairs else 0.0,
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "conflicts": [c.as_dict() for c in conflicts],
    }


def serve_or_refuse(conflicts: Iterable[dict], protected: Iterable[str] = ()) -> dict:
    """What a supervisor is allowed to receive.

    A conflicting slot is served as a conflict rather than as a value, and a
    protected field never leaves the boundary. Both are refusals, and both are
    recorded so the refusal is auditable rather than silent.
    """
    protected_set = {p.lower() for p in protected}
    conflicting = sorted({c["key"] for c in conflicts})
    withheld = sorted(k for k in conflicting if k.lower() in protected_set)
    return {
        "status": "conflict" if conflicting else "consistent",
        "conflicting_slots": conflicting,
        "withheld_protected_slots": withheld,
        "servable": not conflicting and not withheld,
    }
