#!/usr/bin/env python3
"""Fetch real 10-K MD&A (Item 7) narrative text from SEC EDGAR.

The sec_temporal benchmark grounds answers in clean XBRL-derived fact sentences,
which puts grounded accuracy at a ceiling. This module supplies the harder,
realistic corpus: the actual Management's Discussion & Analysis narrative from
the most recent 10-K — noisy prose with rounded figures, YoY comparisons,
distractor numbers, and tables — so the follow-up run can show where the
ceiling moves off 1.00.

Network is confined to ``fetch_*`` / ``latest_10k``; the section-slicing logic
(``extract_mdna``) is pure and unit-tested on synthetic text.
"""

from __future__ import annotations

import json
import re
import os
import urllib.request
import warnings
from dataclasses import dataclass
from typing import Any, List, Optional

USER_AGENT = os.environ.get("SEC_USER_AGENT", "seocho-benchmark support@seocho.io")

# "Item 7. Management's Discussion" — apostrophe may be straight or curly; the
# section header recurs once in the table of contents and once as the body.
_ITEM7_RE = re.compile(r"item\s*7\s*[\.\:\-]?\s*management[’'`s\s]*\s*discussion", re.I)
# Section that follows MD&A — its first hit after the body start marks the end.
_ITEM7A_RE = re.compile(r"item\s*7a\b", re.I)
_ITEM8_RE = re.compile(r"item\s*8\b", re.I)

_MAX_MDNA_CHARS = 40_000  # bound extraction cost; logged when it truncates


@dataclass
class Filing:
    ticker: str
    cik: str
    form: str
    filing_date: str
    report_date: str
    doc_url: str


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Strip an inline-XBRL 10-K HTML document down to whitespace-collapsed text."""
    from bs4 import BeautifulSoup
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # inline-XBRL trips XMLParsedAsHTMLWarning
        text = BeautifulSoup(html, "lxml").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_mdna(text: str) -> str:
    """Return the MD&A (Item 7) section body from full 10-K text.

    The Item-7 header appears in the table of contents first and the body
    later, so the LAST header occurrence is the body start; the section ends at
    the first ``Item 7A`` (or ``Item 8`` fallback) after it. Truncated to
    ``_MAX_MDNA_CHARS`` to bound downstream extraction cost.
    """
    starts = [m.start() for m in _ITEM7_RE.finditer(text)]
    if not starts:
        return ""
    start = starts[-1]  # body, not the TOC entry
    ends = [m.start() for m in _ITEM7A_RE.finditer(text) if m.start() > start]
    if not ends:
        ends = [m.start() for m in _ITEM8_RE.finditer(text) if m.start() > start]
    end = min(ends) if ends else min(len(text), start + _MAX_MDNA_CHARS)
    section = text[start:end].strip()
    return section[:_MAX_MDNA_CHARS]


def chunk_text(text: str, *, size: int = 1200, overlap: int = 150) -> List[str]:
    """Split narrative into overlapping windows for indexing."""
    if not text:
        return []
    out: List[str] = []
    step = max(size - overlap, 1)
    for i in range(0, len(text), step):
        piece = text[i:i + size].strip()
        if piece:
            out.append(piece)
    return out


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _get(url: str, *, raw: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read() if raw else json.load(resp)


def latest_10k(ticker: str, cik: str) -> Optional[Filing]:
    """Resolve the most recent 10-K filing's primary document URL."""
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rec = sub["filings"]["recent"]
    for i, form in enumerate(rec["form"]):
        if form == "10-K":
            accn = rec["accessionNumber"][i].replace("-", "")
            doc = rec["primaryDocument"][i]
            return Filing(
                ticker=ticker, cik=cik, form=form,
                filing_date=rec["filingDate"][i], report_date=rec["reportDate"][i],
                doc_url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}",
            )
    return None


def fetch_mdna(filing: Filing) -> str:
    html = _get(filing.doc_url, raw=True).decode("utf-8", "ignore")
    return extract_mdna(html_to_text(html))


# ---------------------------------------------------------------------------
# Item 8 financial-statement TABLE extraction (S11) — lift the MD&A 0.00 floor
# ---------------------------------------------------------------------------

_YEAR_CELL_RE = re.compile(r"(?<![\d,])(20[1-2]\d)(?![\d,])")
_MONEY_RE = re.compile(r"\(?\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*\)?")


@dataclass
class TableFact:
    concept_id: str
    fiscal_year: int
    value_num: float
    unit: str = "USD"


def _year_in_cell(cell: str) -> Optional[int]:
    m = _YEAR_CELL_RE.search(cell or "")
    return int(m.group(1)) if m else None


def _parse_money(cell: str) -> Optional[float]:
    """Parse a financial-statement cell to a number; '(x)' is negative."""
    if not cell or not any(ch.isdigit() for ch in cell):
        return None
    m = _MONEY_RE.search(cell)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return -val if "(" in cell and ")" in cell else val


def extract_table_facts(html: str, *, registry: Any) -> List[TableFact]:
    """Extract (concept, fiscal_year, value) facts from 10-K financial tables.

    Real-noise extraction: parse HTML <table>s, detect the fiscal-year header
    columns, and for each row whose label resolves to a closed-vocab concept
    (Net sales→Revenue, Net income→NetIncome), read the per-year numeric cells.
    Scale (millions/thousands) is detected from the filing text. First match per
    (concept, year) wins. Best-effort — coverage is bounded by table layout
    variety, which is exactly the floor S11 measures.
    """
    from bs4 import BeautifulSoup

    text = html_to_text(html).lower()
    scale = 1e6 if "in millions" in text else (1e3 if "in thousands" in text else 1.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        soup = BeautifulSoup(html, "lxml")

    facts: List[TableFact] = []
    seen: set = set()
    for table in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                for tr in table.find_all("tr")]
        # find the header row that carries >=2 fiscal-year columns
        year_cols: dict = {}
        for row in rows[:8]:
            tmp = {ci: y for ci, c in enumerate(row) if (y := _year_in_cell(c))}
            if len(tmp) >= 2:
                year_cols = tmp
                break
        if not year_cols:
            continue
        for row in rows:
            if not row:
                continue
            concept = registry.resolve(row[0])
            if not concept:
                continue
            for ci, year in year_cols.items():
                if ci >= len(row):
                    continue
                val = _parse_money(row[ci])
                if val is None:
                    continue
                key = (concept, year)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(TableFact(concept, year, val * scale))
    return facts


def fetch_table_facts(filing: Filing, *, registry: Any) -> List[TableFact]:
    html = _get(filing.doc_url, raw=True).decode("utf-8", "ignore")
    return extract_table_facts(html, registry=registry)
