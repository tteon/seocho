#!/usr/bin/env python3
"""SEC temporal / prior-resistance benchmark generator for SEOCHO.

FinDER originates from SEC 10-K filings, but its famous-company subset is
memorised by the LLM — so model priors mask whether the *graph* actually
contributes (the answer-path A/B work measured judge 0.70/0.70 regardless of
retrieval). This generator builds a **prior-resistant, temporally-labelled**
dataset straight from SEC EDGAR XBRL `companyfacts`:

- gold answers are deterministic XBRL fact values (no LLM-extracted gold, no
  circularity);
- each fact carries a fiscal-year label from its XBRL ``frame`` (e.g.
  ``CY2024``), so the same concept across years tests temporal disambiguation;
- recent fiscal years (period end after the model's training cutoff) are
  provably outside the LLM's priors — the answer must come from the ingested
  filing, which is exactly the graph-contribution signal prior datasets cannot
  isolate.

Output rows reuse the ``graphrag_bench`` JSONL shape
(``corpus`` / ``question`` / ``answer`` / ``gold_entities``) plus temporal
fields (``concept`` / ``fiscal_year`` / ``period_end`` / ``raw_value`` /
``prior_stale``) the measurement runner consumes for the three A/Bs:
temporal-resolution, closed-book-vs-grounded, prior-staleness correction.

Network access (EDGAR) is confined to the ``fetch_*`` helpers; the conversion
logic is pure so it unit-tests without network or an LLM.

Usage::

    python scripts/benchmarks/sec_temporal_bench.py \\
        --tickers AAPL,MSFT,NVDA --years 3 \\
        --out outputs/evaluation/sec_temporal/dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# EDGAR asks every programmatic client to identify itself with a contact.
USER_AGENT = "seocho-benchmark hardy.jeong@xcena.com"

# A full-year duration frame, e.g. "CY2024" — NOT "CY2024Q3" (quarter) or
# "CY2024Q4I" (instant). These canonical annual records dedup the comparatives
# a single 10-K carries and give a clean fiscal-year label in one field.
_ANNUAL_FRAME_RE = re.compile(r"^CY(\d{4})$")

# Each logical metric maps to an ordered list of us-gaap concept tags; filers
# differ (some report Revenues, others RevenueFromContractWithCustomer...), so
# the first concept that yields annual facts wins.
CONCEPT_GROUPS: List[Dict[str, Any]] = [
    {
        "metric": "revenue",
        "phrase": "total revenue",
        "concepts": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
    },
    {
        "metric": "net_income",
        "phrase": "net income",
        "concepts": ["NetIncomeLoss", "ProfitLoss"],
    },
    {
        "metric": "total_assets",
        "phrase": "total assets",
        "concepts": ["Assets"],
    },
    {
        "metric": "stockholders_equity",
        "phrase": "stockholders' equity",
        "concepts": [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
    },
]


# ---------------------------------------------------------------------------
# Pure conversion logic (no network, no LLM) — unit-tested
# ---------------------------------------------------------------------------


def fiscal_year_from_frame(frame: Optional[str]) -> Optional[int]:
    """Return the fiscal year for a full-year XBRL ``frame`` (``CY2024``→2024).

    Quarterly / instant / missing frames return ``None`` so callers can skip
    them — only annual duration facts make clean year-labelled gold.
    """
    if not frame:
        return None
    m = _ANNUAL_FRAME_RE.match(frame)
    return int(m.group(1)) if m else None


def select_annual_facts(units: Dict[str, Any], n_years: int) -> List[Dict[str, Any]]:
    """Pick the most recent ``n_years`` annual facts from a concept's ``units``.

    Keeps only full-year-framed records (one per fiscal year), dedups by
    fiscal year (first frame wins — companyfacts lists the canonical value
    first), and returns newest-first with a normalised shape.
    """
    # Prefer USD; fall back to the first declared unit (e.g. shares-based).
    unit_key = "USD" if "USD" in units else (next(iter(units), None))
    if unit_key is None:
        return []
    out: Dict[int, Dict[str, Any]] = {}
    for rec in units[unit_key]:
        fy = fiscal_year_from_frame(rec.get("frame"))
        if fy is None:
            continue
        if rec.get("form") != "10-K":
            continue
        if fy in out:
            continue  # canonical (first) record for that year already taken
        out[fy] = {
            "fiscal_year": fy,
            "period_end": rec.get("end"),
            "value": rec.get("val"),
            "unit": unit_key,
            "accn": rec.get("accn"),
        }
    ordered = sorted(out.values(), key=lambda r: r["fiscal_year"], reverse=True)
    return ordered[:n_years]


def format_value(value: Any, unit: str) -> str:
    """Render an XBRL value as a human gold string (``$391,035 million``).

    USD values are normalised to millions with thousands separators — the
    granularity FinDER answers use — so substring/contains scoring lines up
    with how a model naturally states a figure.
    """
    if unit == "USD" and isinstance(value, (int, float)):
        millions = value / 1_000_000
        return f"${millions:,.0f} million"
    return f"{value} {unit}".strip()


def pick_concept(
    usgaap: Dict[str, Any], group: Dict[str, Any], n_years: int
) -> Optional[Dict[str, Any]]:
    """Merge annual facts across all concepts in the group, newest ``n_years``.

    Filers migrate concept tags over time (e.g. ``Revenues`` →
    ``RevenueFromContractWithCustomerExcludingAssessedTax``), so picking the
    first concept that has *any* facts can return stale years while a sibling
    tag carries the recent ones. Merging across the group and deduping by
    fiscal year (earlier concept in the list wins ties) guarantees the most
    recent reported years surface regardless of which tag holds them.
    """
    by_year: Dict[int, Dict[str, Any]] = {}
    concept_for_year: Dict[int, str] = {}
    for concept in group["concepts"]:
        node = usgaap.get(concept)
        if not node:
            continue
        for fact in select_annual_facts(node.get("units", {}), n_years=10_000):
            fy = fact["fiscal_year"]
            if fy not in by_year:  # earlier concept in the group wins the year
                by_year[fy] = fact
                concept_for_year[fy] = concept
    if not by_year:
        return None
    facts = sorted(by_year.values(), key=lambda r: r["fiscal_year"], reverse=True)[
        :n_years
    ]
    # report the concept backing the newest selected year (diagnostic only)
    primary_concept = concept_for_year[facts[0]["fiscal_year"]]
    return {"concept": primary_concept, "facts": facts}


def build_qa_rows(
    company: str,
    ticker: str,
    usgaap: Dict[str, Any],
    *,
    n_years: int,
    cutoff_year: int,
    min_fiscal_year: int = 0,
) -> List[Dict[str, Any]]:
    """Build temporal Q/A rows for one company across metrics × recent years.

    ``cutoff_year`` marks the model's training horizon: facts whose fiscal year
    is strictly greater are tagged ``prior_stale=True`` (the prior cannot know
    them, so a correct answer must come from the graph). ``min_fiscal_year``
    drops years older than the recent window — some filers' revenue/income
    sit under non-standard tags whose only framed records are stale, and those
    old years are noise for a recent-temporal test.
    """
    rows: List[Dict[str, Any]] = []
    for group in CONCEPT_GROUPS:
        picked = pick_concept(usgaap, group, n_years)
        if not picked:
            continue
        concept = picked["concept"]
        picked["facts"] = [
            f for f in picked["facts"] if f["fiscal_year"] >= min_fiscal_year
        ]
        if not picked["facts"]:
            continue
        # Corpus: one fact-sentence per year for this metric. Indexed together,
        # they force the lane to disambiguate BOTH metric and year at retrieval.
        corpus = [
            f"In fiscal year {f['fiscal_year']}, {company} reported "
            f"{group['phrase']} of {format_value(f['value'], f['unit'])} "
            f"(period ended {f['period_end']})."
            for f in picked["facts"]
        ]
        for f in picked["facts"]:
            rows.append(
                {
                    "corpus": corpus,
                    "question": (
                        f"What was {company}'s {group['phrase']} "
                        f"for fiscal year {f['fiscal_year']}?"
                    ),
                    "answer": format_value(f["value"], f["unit"]),
                    "gold_entities": [company, group["metric"]],
                    # temporal / diagnostic fields
                    "ticker": ticker,
                    "metric": group["metric"],
                    "concept": concept,
                    "fiscal_year": f["fiscal_year"],
                    "period_end": f["period_end"],
                    "raw_value": f["value"],
                    "unit": f["unit"],
                    "prior_stale": f["fiscal_year"] > cutoff_year,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# EDGAR network helpers (the only impure surface)
# ---------------------------------------------------------------------------


def _get_json(url: str, *, retries: int = 3, pause: float = 0.4) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 — surface after retries
            last = exc
            time.sleep(pause * (attempt + 1))
    raise RuntimeError(f"EDGAR GET failed after {retries}: {url}: {last}")


def resolve_ciks(tickers: Sequence[str]) -> Dict[str, str]:
    """Map tickers → 10-digit zero-padded CIK via SEC's ticker index."""
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    by_ticker = {v["ticker"].upper(): v for v in data.values()}
    out: Dict[str, str] = {}
    for t in tickers:
        ent = by_ticker.get(t.upper())
        if ent:
            out[t.upper()] = str(ent["cik_str"]).zfill(10)
    return out


def fetch_companyfacts(cik: str) -> Dict[str, Any]:
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")


def generate(
    tickers: Sequence[str],
    *,
    n_years: int,
    cutoff_year: int,
    min_fiscal_year: int = 0,
    pause: float = 0.3,
) -> List[Dict[str, Any]]:
    ciks = resolve_ciks(tickers)
    rows: List[Dict[str, Any]] = []
    for t in tickers:
        cik = ciks.get(t.upper())
        if not cik:
            print(f"  [skip] no CIK for {t}", file=sys.stderr)
            continue
        try:
            cf = fetch_companyfacts(cik)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {t}: {exc}", file=sys.stderr)
            continue
        company = cf.get("entityName", t)
        usgaap = cf.get("facts", {}).get("us-gaap", {})
        company_rows = build_qa_rows(
            company,
            t.upper(),
            usgaap,
            n_years=n_years,
            cutoff_year=cutoff_year,
            min_fiscal_year=min_fiscal_year,
        )
        rows.extend(company_rows)
        print(f"  [{t}] {company}: {len(company_rows)} rows", file=sys.stderr)
        time.sleep(pause)
    return rows


# The 20-company basket (FinDER-aligned large-cap issuers). Mutable via --tickers.
DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "JPM",
    "BRK-B",
    "V",
    "JNJ",
    "WMT",
    "PG",
    "XOM",
    "HD",
    "KO",
    "PEP",
    "CSCO",
    "INTC",
    "CVX",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC temporal benchmark generator")
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help="Comma-separated tickers (default: 20-company basket)",
    )
    parser.add_argument(
        "--years", type=int, default=3, help="Most recent N fiscal years per metric"
    )
    parser.add_argument(
        "--cutoff-year",
        type=int,
        default=2024,
        help="Model training-horizon FY; facts after are prior_stale",
    )
    parser.add_argument(
        "--min-fiscal-year",
        type=int,
        default=None,
        help="Drop years older than this (default: cutoff-year - 2)",
    )
    parser.add_argument("--out", default="-", help="Output JSONL path (- for stdout)")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    min_fy = (
        args.min_fiscal_year
        if args.min_fiscal_year is not None
        else args.cutoff_year - 2
    )
    rows = generate(
        tickers,
        n_years=args.years,
        cutoff_year=args.cutoff_year,
        min_fiscal_year=min_fy,
    )

    stale = sum(1 for r in rows if r["prior_stale"])
    print(
        f"\nGenerated {len(rows)} rows ({stale} prior-stale) "
        f"across {len(tickers)} tickers",
        file=sys.stderr,
    )

    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if args.out == "-":
        print(payload)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
