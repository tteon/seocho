"""Entity → CIK resolution (ADR-0103, semantic layer).

Canonical entity identity is the SEC CIK, not a free-text name. The writer
stamps the resolved CIK on each observation; the reader resolves a question's
entity to the SAME CIK and matches on equality — replacing the brittle
``c.name CONTAINS`` OR-chain.

Resolution is deterministic: exact ticker, then normalized-name exact match.
The CIK table is built OFFLINE (from SEC's company_tickers index) and frozen;
this module only does O(1) lookups (no network on the hot path). The default
table is a small confirmed seed; the full table is loaded via `from_ticker_map`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional

_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|"
    r"lp|llc|holdings?|group|the)\b",
    re.I,
)


def normalize_name(name: str) -> str:
    """Lowercase, strip legal suffixes/punctuation, collapse whitespace."""
    s = str(name).lower()
    s = re.sub(r"[.,&]", " ", s)
    s = _SUFFIX_RE.sub(" ", s)
    return " ".join(s.split())


@dataclass
class EntityResolver:
    """Resolves a surface name or ticker to a canonical 10-digit CIK."""

    cik_by_ticker: Dict[str, str] = field(default_factory=dict)
    cik_by_name: Dict[str, str] = field(default_factory=dict)   # normalized → cik

    def resolve(self, surface: str) -> Optional[str]:
        if not surface:
            return None
        s = surface.strip()
        # 1) exact ticker (uppercase)
        cik = self.cik_by_ticker.get(s.upper())
        if cik:
            return cik
        # 2) normalized-name exact
        return self.cik_by_name.get(normalize_name(s))

    @classmethod
    def from_ticker_map(
        cls,
        cik_by_ticker: Dict[str, str],
        name_by_ticker: Optional[Dict[str, str]] = None,
    ) -> "EntityResolver":
        ticker_map = {t.upper(): str(c).zfill(10) for t, c in cik_by_ticker.items()}
        name_map: Dict[str, str] = {}
        for t, name in (name_by_ticker or {}).items():
            cik = ticker_map.get(t.upper())
            if cik:
                name_map[normalize_name(name)] = cik
        return cls(cik_by_ticker=ticker_map, cik_by_name=name_map)


# Small confirmed seed (verified against SEC company_tickers). The full frozen
# table is built offline; this lets the resolver work for the benchmark basket.
_SEED_TICKER_CIK = {
    "AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810",
    "GOOGL": "0001652044", "AMZN": "0001018724",
}
_SEED_NAME = {
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corp", "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com, Inc.",
}


def default_resolver() -> EntityResolver:
    return EntityResolver.from_ticker_map(_SEED_TICKER_CIK, _SEED_NAME)
