"""Numeric-fact validation as SOFT, precision-first signals (ADR-0127).

ADR-0119's P3 experiment found financial numeric errors are ~91% *structural*
(wrong/missing/mis-typed extracted fact) and that constraint checks catch ~94% of
them (high recall) — but a naive validator flagged ~91% of *correct* answers too
(terrible precision), largely because rigid unit/scale enums mis-fire (the model
puts "millions" in the ``unit`` field) and a required ``period`` is too strict.

This module is the precision-first redesign: every finding is SOFT (``info`` /
``warn`` — never a hard reject), unit/scale are NORMALIZED before any check, a
missing period is ``info`` not ``warn``, and the highest-value check is
RECONCILIATION (sum-of-parts ≈ total) which catches a wrong number pulled without
recomputing the answer. Pure/offline; the result is a confidence + repair
suggestions a caller can use as a soft re-ask trigger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_SCALE_TOKENS = {
    "thousand": "thousand", "thousands": "thousand", "k": "thousand",
    "million": "million", "millions": "million", "mm": "million", "mn": "million", "m": "million",
    "billion": "billion", "billions": "billion", "bn": "billion", "b": "billion",
    "ones": "", "absolute": "", "units": "",
}
_UNIT_TOKENS = {
    "$": "usd", "usd": "usd", "dollar": "usd", "dollars": "usd",
    "€": "eur", "eur": "eur", "£": "gbp", "gbp": "gbp", "jpy": "jpy", "¥": "jpy",
    "%": "percent", "percent": "percent", "pct": "percent",
    "shares": "shares", "share": "shares", "x": "ratio", "ratio": "ratio", "bps": "bps",
}
_NEGATIVE_IMPLAUSIBLE = re.compile(r"revenue|assets?|shares?|cash|capitalization|market\s*cap", re.I)
_PERIOD_RE = re.compile(r"(19|20)\d{2}|q[1-4]|fy|h[12]|first|second|third|fourth|quarter|annual", re.I)


def normalize_scale(s: str) -> str:
    return _SCALE_TOKENS.get(str(s or "").strip().lower(), str(s or "").strip().lower())


def normalize_unit(s: str) -> str:
    return _UNIT_TOKENS.get(str(s or "").strip().lower(), str(s or "").strip().lower())


@dataclass(slots=True)
class NumericFact:
    name: str = ""
    value: Optional[float] = None
    unit: str = ""
    scale: str = ""
    period: str = ""
    company: str = ""
    raw_value: Any = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NumericFact":
        """Tolerant parse. CRITICAL precision fix (ADR-0119): a scale word that the
        model placed in ``unit`` (e.g. "millions") is recognised as a SCALE and
        moved, rather than flagged as an off-vocabulary unit."""
        raw = d.get("value")
        try:
            value = float(str(raw).replace(",", "").replace("$", "").replace("%", "").strip()) if raw not in (None, "") else None
        except Exception:
            value = None
        unit = str(d.get("unit", "")).strip()
        scale = str(d.get("scale", "")).strip()
        # if the "unit" is actually a scale word, relocate it
        if not scale and unit.lower() in _SCALE_TOKENS:
            scale, unit = unit, ""
        return cls(
            name=str(d.get("name", "")), value=value,
            unit=normalize_unit(unit), scale=normalize_scale(scale),
            period=str(d.get("period", "")).strip(), company=str(d.get("company", "")).strip(),
            raw_value=raw,
        )


@dataclass(slots=True)
class NumericFinding:
    severity: str          # "info" | "warn"  — never a hard reject
    code: str
    fact: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "fact": self.fact, "message": self.message}


@dataclass(slots=True)
class NumericValidationResult:
    findings: List[NumericFinding] = field(default_factory=list)
    confidence: float = 1.0
    repairs: List[str] = field(default_factory=list)

    @property
    def warnings(self) -> List[NumericFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "confidence": round(self.confidence, 4),
            "repairs": list(self.repairs),
        }


def validate_numeric_facts(
    facts: List[Dict[str, Any]],
    *,
    source_text: Optional[str] = None,
) -> NumericValidationResult:
    """Soft, precision-first validation. Confidence drops only on ``warn`` findings;
    ``info`` (relaxed signals like a missing period or unknown unit) does not.

    When ``source_text`` is supplied, also grounds each value against the numbers
    present in the source (ADR-0131): an extracted value absent from the source is
    a ``warn`` (``ungrounded_value``) — the recall lever for wrong/fabricated
    numbers that isolated-fact rules cannot see (ADR-0130)."""
    findings: List[NumericFinding] = []
    repairs: List[str] = []
    parsed = [NumericFact.from_dict(f) for f in facts if isinstance(f, dict)]

    for fct in parsed:
        nm = fct.name or "(unnamed)"
        if fct.raw_value not in (None, "") and fct.value is None:
            findings.append(NumericFinding("warn", "value_not_numeric", nm,
                                           f"value {fct.raw_value!r} does not parse as a number"))
            repairs.append(f"re-extract a numeric value for '{nm}'")
        if not fct.period:
            findings.append(NumericFinding("info", "missing_period", nm,
                                           "no fiscal period — relaxed (info), supply if known"))
        elif not _PERIOD_RE.search(fct.period):
            findings.append(NumericFinding("info", "period_unrecognized", nm,
                                           f"period '{fct.period}' not a recognizable fiscal period"))
        if fct.value is not None and fct.value < 0 and _NEGATIVE_IMPLAUSIBLE.search(nm):
            findings.append(NumericFinding("warn", "implausible_sign", nm,
                                           f"negative value {fct.value} is implausible for '{nm}'"))
            repairs.append(f"check the sign of '{nm}'")

    # reconciliation across grouped facts
    for group in find_reconciliation_groups(parsed):
        finding = reconcile(group["parts"], group["total"])
        if finding is not None:
            findings.append(finding)
            repairs.append(f"reconcile components of '{group['total'].name}'")

    # source grounding (the recall lever — ADR-0131)
    if source_text:
        grounding = ground_facts(facts, source_text)
        findings.extend(grounding.findings)
        repairs.extend(f"verify '{f.fact}' against the source" for f in grounding.findings)

    n_warn = sum(1 for f in findings if f.severity == "warn")
    confidence = max(0.0, 1.0 - 0.34 * n_warn)
    return NumericValidationResult(findings=findings, confidence=confidence, repairs=repairs)


def reconcile(parts: List[NumericFact], total: NumericFact, *, rel_tol: float = 0.01) -> Optional[NumericFinding]:
    """Warn when the parts do not sum to the stated total (beyond ``rel_tol``).
    Catches a wrong number pulled WITHOUT recomputing the answer."""
    vals = [p.value for p in parts if p.value is not None]
    if not vals or total.value is None:
        return None
    s = sum(vals)
    denom = abs(total.value) if total.value else 1.0
    if abs(s - total.value) / denom > rel_tol:
        return NumericFinding("warn", "reconciliation", total.name or "(total)",
                              f"sum of parts {s} != stated total {total.value} "
                              f"(parts: {[p.name for p in parts]})")
    return None


def find_reconciliation_groups(facts: List[NumericFact]) -> List[Dict[str, Any]]:
    """Heuristic grouping: a fact whose name contains 'total'/'net'/'sum' is a
    candidate total; the remaining facts sharing a token stem with it are its
    parts. Deliberately simple — explicit groups should be passed to
    :func:`reconcile` directly when available."""
    groups: List[Dict[str, Any]] = []
    totals = [f for f in facts if re.search(r"\btotal\b|\bnet\b|\bsum\b", f.name, re.I)]
    for total in totals:
        stem = re.sub(r"\b(total|net|sum)\b", "", total.name, flags=re.I).strip().lower()
        parts = [f for f in facts if f is not total and stem and stem in f.name.lower()]
        if len(parts) >= 2:
            groups.append({"total": total, "parts": parts})
    return groups


# ---------------------------------------------------------------------------
# Source-grounded numeric check (ADR-0131): the recall lever ADR-0130 identified.
# The dominant structural error is "wrong number pulled" — a plausible value that
# is wrong. Isolated-fact rules can't see it, but we CAN check whether an
# extracted value actually appears in the source text. A value absent from the
# source was fabricated or mis-computed → flag it.
# ---------------------------------------------------------------------------

_SCALE_MULT = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
# a number, optionally $-prefixed / %-suffixed / parenthesised-negative, with commas/decimals
_NUM_RE = re.compile(r"\(?\$?\s*-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\(?\$?\s*-?\d+(?:\.\d+)?")
_SCALE_AFTER_RE = re.compile(r"\s*(thousand|million|billion)s?\b", re.I)


def extract_source_numbers(text: str) -> List[float]:
    """All numeric values present in the source text, including scale-expanded
    forms (``$539.2 million`` contributes both 539.2 and 539_200_000) and
    parenthesised negatives. Used to ground extracted fact values."""
    if not text:
        return []
    out: set = set()
    for m in _NUM_RE.finditer(text):
        tok = m.group(0)
        neg = tok.strip().startswith("(")
        cleaned = tok.replace("(", "").replace(")", "").replace("$", "").replace(",", "").strip()
        try:
            val = float(cleaned)
        except Exception:
            continue
        if neg:
            val = -abs(val)
        out.add(val)
        # scale word immediately after the number → add the scaled value too
        after = text[m.end():m.end() + 12]
        sm = _SCALE_AFTER_RE.match(after)
        if sm:
            out.add(round(val * _SCALE_MULT[sm.group(1).lower()], 6))
    return sorted(out)


def _matches(value: float, source_numbers: List[float], *, rel_tol: float, scale: str = "") -> bool:
    candidates = [value]
    mult = _SCALE_MULT.get((scale or "").strip().lower())
    if mult:
        candidates += [value * mult, value / mult]
    for c in candidates:
        for s in source_numbers:
            denom = max(abs(s), abs(c), 1.0)
            if abs(c - s) / denom <= rel_tol:
                return True
    return False


@dataclass(slots=True)
class GroundingResult:
    findings: List[NumericFinding] = field(default_factory=list)
    grounded: int = 0
    checked: int = 0

    @property
    def grounded_ratio(self) -> float:
        return round(self.grounded / self.checked, 4) if self.checked else 1.0

    @property
    def any_ungrounded(self) -> bool:
        return any(f.code == "ungrounded_value" for f in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {"findings": [f.to_dict() for f in self.findings],
                "grounded": self.grounded, "checked": self.checked,
                "grounded_ratio": self.grounded_ratio}


def ground_facts(facts: List[Dict[str, Any]], source_text: str, *, rel_tol: float = 0.01) -> GroundingResult:
    """Check each extracted fact's value against the numbers actually present in
    ``source_text``. An extracted value with no source match is ``warn``
    (``ungrounded_value``) — the catch for fabricated / mis-computed numbers that
    isolated-fact rules (ADR-0130) cannot see."""
    src = extract_source_numbers(source_text)
    res = GroundingResult()
    for f in facts:
        if not isinstance(f, dict):
            continue
        fct = NumericFact.from_dict(f)
        if fct.value is None:
            continue
        res.checked += 1
        if _matches(fct.value, src, rel_tol=rel_tol, scale=fct.scale):
            res.grounded += 1
        else:
            res.findings.append(NumericFinding(
                "warn", "ungrounded_value", fct.name or "(unnamed)",
                f"value {fct.value} not found in the source text (possible wrong/fabricated number)"))
    return res
