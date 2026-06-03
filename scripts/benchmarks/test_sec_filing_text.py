"""Unit tests for sec_filing_text.py — no network. Tests the pure MD&A slicing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sec_filing_text as ft


# A miniature 10-K text: table of contents (Item 7 header appears first here)
# followed by the real section bodies.
_TOC = (
    "Apple Inc. Form 10-K Table of Contents "
    "Item 7. Management's Discussion and Analysis of Financial Condition 21 "
    "Item 7A. Quantitative and Qualitative Disclosures About Market Risk 27 "
    "Item 8. Financial Statements and Supplementary Data 28 "
)
_BODY = (
    "Item 7. Management's Discussion and Analysis of Financial Condition and "
    "Results of Operations. Net sales increased 5% to $416.2 billion in fiscal "
    "2025 compared to $391.0 billion in fiscal 2024. "
)
_AFTER = (
    "Item 7A. Quantitative and Qualitative Disclosures About Market Risk. "
    "Interest rate risk discussion follows. "
    "Item 8. Financial Statements. The accompanying notes... "
)


def test_extract_mdna_picks_body_not_toc():
    text = _TOC + _BODY + _AFTER
    mdna = ft.extract_mdna(text)
    # starts at the BODY header, not the TOC entry
    assert mdna.startswith("Item 7. Management's Discussion")
    assert "Net sales increased 5% to $416.2 billion" in mdna
    # stops before Item 7A (does not bleed into market-risk / Item 8)
    assert "Interest rate risk" not in mdna
    assert "accompanying notes" not in mdna


def test_extract_mdna_falls_back_to_item8_when_no_7a():
    text = _TOC + _BODY + "Item 8. Financial Statements follow."
    mdna = ft.extract_mdna(text)
    assert "Net sales increased 5%" in mdna
    assert "Financial Statements follow" not in mdna


def test_extract_mdna_empty_when_no_item7():
    assert ft.extract_mdna("No discussion section in this document at all.") == ""


def test_extract_mdna_truncates_to_cap(monkeypatch):
    monkeypatch.setattr(ft, "_MAX_MDNA_CHARS", 50)
    body = "Item 7. Management's Discussion " + ("x" * 500)
    mdna = ft.extract_mdna(body)
    assert len(mdna) <= 50


def test_chunk_text_overlaps_and_covers():
    chunks = ft.chunk_text("abcdefghij" * 30, size=100, overlap=20)
    assert len(chunks) >= 3
    # contiguous coverage: every chunk non-empty
    assert all(c for c in chunks)


def test_chunk_text_empty():
    assert ft.chunk_text("") == []
