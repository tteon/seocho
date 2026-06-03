"""Canonical period model (ADR-0103, semantic layer).

Replaces the fuzzy ``CONTAINS year`` matching that silently zeroed the metric
query. A period is a typed tuple normalized to a canonical key so writer and
reader filter on equality, not substring.

Canonical key form: ``{basis}:{fiscal_year}:{fiscal_period}`` — e.g.
``fiscal:2024:FY``, ``fiscal:2024:Q3``. Fiscal is the default basis (10-K
filings are fiscal); calendar is supported for completeness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_QUARTER_RE = re.compile(r"\bq([1-4])\b", re.I)
# 4-digit year, not glued to other digits — matches "FY2024", "2024", "Q3 2024"
# but not "12024"/"20245". Avoids \b (no boundary between "FY" and "2024").
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


@dataclass(frozen=True, slots=True)
class Period:
    fiscal_year: int
    fiscal_period: str = "FY"      # FY | Q1 | Q2 | Q3 | Q4
    basis: str = "fiscal"          # fiscal | calendar

    @property
    def key(self) -> str:
        return f"{self.basis}:{self.fiscal_year}:{self.fiscal_period}"


def parse_period(raw: str) -> Optional[Period]:
    """Parse a free-form period phrase into a typed Period, or None.

    Handles "FY2024", "fiscal 2024", "FY 2024", "2024", "Q3 2024",
    "third quarter 2024" → Period. Year is required; quarter optional
    (defaults to full-year FY). Calendar basis when the phrase says "calendar".
    """
    if not raw:
        return None
    text = str(raw).strip().lower()
    ym = _YEAR_RE.search(text)
    if not ym:
        return None
    year = int(ym.group(0))

    qm = _QUARTER_RE.search(text)
    if qm:
        period = f"Q{qm.group(1)}"
    else:
        for word, q in (("first quarter", "Q1"), ("second quarter", "Q2"),
                        ("third quarter", "Q3"), ("fourth quarter", "Q4")):
            if word in text:
                period = q
                break
        else:
            period = "FY"

    basis = "calendar" if "calendar" in text else "fiscal"
    return Period(fiscal_year=year, fiscal_period=period, basis=basis)


def normalize_period(raw: str) -> Optional[str]:
    """Free-form period phrase → canonical period key, or None if unparseable."""
    p = parse_period(raw)
    return p.key if p else None
