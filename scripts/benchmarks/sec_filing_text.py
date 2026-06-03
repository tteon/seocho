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
import urllib.request
import warnings
from dataclasses import dataclass
from typing import List, Optional

USER_AGENT = "seocho-benchmark hardy.jeong@xcena.com"

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
