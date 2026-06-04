"""Entity → CIK resolution (ADR-0103, semantic layer).

Canonical entity identity is the SEC CIK, not a free-text name. The writer
stamps the resolved CIK on each observation; the reader resolves a question's
entity to the SAME CIK and matches on equality — replacing the brittle
``c.name CONTAINS`` OR-chain.

Resolution is deterministic: exact ticker, then normalized-name exact match.
The CIK table is built OFFLINE (from SEC's company_tickers index) and frozen;
this module only does O(1) lookups (no network on the hot path). `default_resolver`
loads the full frozen table (~10k issuers) built by scripts/build_cik_table.py,
falling back to a small confirmed seed when that resource is absent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

_FROZEN_TABLE = Path(__file__).resolve().parent / "cik_table.json"

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

    @classmethod
    def from_frozen(cls, path: "Path" = _FROZEN_TABLE) -> Optional["EntityResolver"]:
        """Load the full offline-built table (by_ticker + by_name), or None."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        by_ticker = {str(k).upper(): str(v) for k, v in data.get("by_ticker", {}).items()}
        by_name = {str(k): str(v) for k, v in data.get("by_name", {}).items()}
        if not by_ticker and not by_name:
            return None
        return cls(cik_by_ticker=by_ticker, cik_by_name=by_name)


# Small confirmed seed (verified against SEC company_tickers) — the fallback when
# the frozen full table is absent (e.g. before scripts/build_cik_table.py runs).
_SEED_TICKER_CIK = {
    "AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810",
    "GOOGL": "0001652044", "AMZN": "0001018724",
}
_SEED_NAME = {
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corp", "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com, Inc.",
}

_DEFAULT_RESOLVER: Optional[EntityResolver] = None


def default_resolver() -> EntityResolver:
    """Full frozen table if present (cached), else the 5-company seed."""
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = (EntityResolver.from_frozen()
                             or EntityResolver.from_ticker_map(_SEED_TICKER_CIK, _SEED_NAME))
    return _DEFAULT_RESOLVER
