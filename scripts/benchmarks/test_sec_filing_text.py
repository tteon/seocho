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


# ---- Item 8 table extraction (S11) ------------------------------------------

_INCOME_TABLE_HTML = """
<p>CONSOLIDATED STATEMENTS OF OPERATIONS (In millions)</p>
<table>
  <tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>
  <tr><td>Net sales</td><td>416,161</td><td>391,035</td><td>383,285</td></tr>
  <tr><td>Cost of sales</td><td>210,352</td><td>210,352</td><td>214,137</td></tr>
  <tr><td>Net income</td><td>112,010</td><td>93,736</td><td>96,995</td></tr>
</table>
"""


def _registry():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from seocho.semantic_layer import default_registry
    return default_registry()


def test_extract_table_facts_income_statement():
    facts = ft.extract_table_facts(_INCOME_TABLE_HTML, registry=_registry())
    by = {(f.concept_id, f.fiscal_year): f.value_num for f in facts}
    assert by[("metric:Revenue", 2025)] == 416_161_000_000.0     # scaled by millions
    assert by[("metric:Revenue", 2024)] == 391_035_000_000.0
    assert by[("metric:NetIncome", 2025)] == 112_010_000_000.0
    # "Cost of sales" is out of the closed vocab -> not extracted
    assert all(f.concept_id in ("metric:Revenue", "metric:NetIncome") for f in facts)


def test_extract_table_facts_no_year_header_returns_empty():
    html = "<table><tr><td>Net sales</td><td>100</td></tr></table>"
    assert ft.extract_table_facts(html, registry=_registry()) == []


def test_parse_money_handles_parens_and_commas():
    assert ft._parse_money("416,161") == 416161.0
    assert ft._parse_money("(1,234)") == -1234.0
    assert ft._parse_money("$391,035") == 391035.0
    assert ft._parse_money("—") is None
