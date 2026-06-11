#!/usr/bin/env python3
"""Build the frozen entity→CIK table for the semantic-layer EntityResolver (H1).

Offline build step: pulls SEC's public company_tickers.json once and writes a
compact frozen resource the EntityResolver loads at runtime (network only here,
O(1) lookups on the hot path). Run when refreshing the universe:

    python scripts/build_cik_table.py

Writes src/seocho/semantic_layer/data/cik_table.json:
  {"by_ticker": {"AAPL": "0000320193", ...},
   "by_name":   {"apple": "0000320193", ...}}   # normalized title -> cik
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from seocho.semantic_layer.identity import normalize_name  # noqa: E402

UA = {"User-Agent": "seocho-ingest hardy.jeong@xcena.com"}
OUT = ROOT / "src" / "seocho" / "semantic_layer" / "cik_table.json"


def main() -> int:
    req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)

    by_ticker: dict = {}
    by_name: dict = {}
    for ent in data.values():
        cik = str(ent["cik_str"]).zfill(10)
        ticker = str(ent.get("ticker", "")).upper().strip()
        if ticker:
            by_ticker.setdefault(ticker, cik)
        norm = normalize_name(str(ent.get("title", "")))
        if norm:
            by_name.setdefault(norm, cik)   # first title wins on collision

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"by_ticker": by_ticker, "by_name": by_name}
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} — {len(by_ticker)} tickers, {len(by_name)} names, "
          f"{OUT.stat().st_size // 1024} KB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
