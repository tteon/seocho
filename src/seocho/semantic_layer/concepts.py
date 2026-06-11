"""Closed metric-concept taxonomy (ADR-0103, semantic layer).

The ontology used as a SEMANTIC LAYER: a closed SKOS-style vocabulary of metric
concepts with alias surface forms. The LLM decomposer SELECTS a concept from
this set (never invents a free-text name); extraction maps a surface metric to
the same canonical concept_id. Both sides share this registry, so they cannot
drift.

`resolve()` here is an EXACT (lowercased) alias lookup — deterministic. Fuzzy
surface→concept grounding (bge) lives in the query layer and scores against
`concepts` as its candidate set; this module is the closed-set authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True, slots=True)
class MetricConcept:
    concept_id: str                       # canonical id, e.g. "metric:Revenue"
    pref_label: str                       # "Revenue"
    alt_labels: Tuple[str, ...] = ()       # surface synonyms
    unit_class: str = "currency"          # currency | count | ratio | shares
    xbrl: Tuple[str, ...] = ()            # us-gaap authority tags (XBRL ingest)
    period_type: str = "duration"         # duration (income stmt) | instant (balance sheet)


class ConceptRegistry:
    """Closed set of MetricConcepts with an exact alias index."""

    def __init__(self, concepts: Tuple[MetricConcept, ...]):
        self._by_id: Dict[str, MetricConcept] = {c.concept_id: c for c in concepts}
        self._alias_to_id: Dict[str, str] = {}
        self._xbrl_to_id: Dict[str, str] = {}
        for c in concepts:
            for surface in (c.pref_label, c.concept_id, *c.alt_labels):
                self._alias_to_id[self._norm(surface)] = c.concept_id
            for tag in c.xbrl:
                # store both "us-gaap:Revenues" and the bare "Revenues"
                self._xbrl_to_id[tag] = c.concept_id
                self._xbrl_to_id[tag.split(":", 1)[-1]] = c.concept_id

    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    def resolve(self, surface: str) -> Optional[str]:
        """Exact (normalized) surface → concept_id, or None if out of vocabulary."""
        return self._alias_to_id.get(self._norm(surface))

    def resolve_xbrl(self, tag: str) -> Optional[str]:
        """us-gaap tag ('Revenues' or 'us-gaap:Revenues') → concept_id."""
        return self._xbrl_to_id.get(str(tag).strip())

    @property
    def xbrl_map(self) -> Dict[str, str]:
        """Bare us-gaap tag → concept_id (for iterating companyfacts)."""
        return {t: cid for t, cid in self._xbrl_to_id.items() if ":" not in t}

    def is_member(self, concept_id: str) -> bool:
        return concept_id in self._by_id

    def get(self, concept_id: str) -> Optional[MetricConcept]:
        return self._by_id.get(concept_id)

    @property
    def concepts(self) -> Tuple[MetricConcept, ...]:
        return tuple(self._by_id.values())

    @property
    def candidate_surfaces(self) -> Tuple[str, ...]:
        """All pref/alt labels — the candidate set for fuzzy grounding (S6)."""
        return tuple(self._alias_to_id.keys())


# ---------------------------------------------------------------------------
# Default finance taxonomy — seeded with the duration metrics the prior-
# resistant SEC benchmark covers (revenue, net income). Extended additively.
# ---------------------------------------------------------------------------

DEFAULT_FINANCE_CONCEPTS: Tuple[MetricConcept, ...] = (
    MetricConcept(
        concept_id="metric:Revenue",
        pref_label="Revenue",
        alt_labels=("revenues", "total revenue", "net sales", "sales",
                    "turnover", "topline", "total net sales"),
        unit_class="currency",
        xbrl=("us-gaap:Revenues",
              "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
    ),
    MetricConcept(
        concept_id="metric:NetIncome",
        pref_label="Net Income",
        alt_labels=("net earnings", "earnings", "profit", "net profit",
                    "bottom line", "net income loss"),
        unit_class="currency",
        xbrl=("us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"),
    ),
    # --- income-statement (duration) metrics ---
    MetricConcept(
        concept_id="metric:GrossProfit",
        pref_label="Gross Profit",
        alt_labels=("gross margin", "gross income"),
        unit_class="currency",
        xbrl=("us-gaap:GrossProfit",),
    ),
    MetricConcept(
        concept_id="metric:OperatingIncome",
        pref_label="Operating Income",
        alt_labels=("operating profit", "operating earnings", "ebit",
                    "income from operations", "operating income loss"),
        unit_class="currency",
        xbrl=("us-gaap:OperatingIncomeLoss",),
    ),
    MetricConcept(
        concept_id="metric:EPS",
        pref_label="Diluted EPS",
        alt_labels=("eps", "earnings per share", "diluted earnings per share",
                    "earnings per share diluted"),
        unit_class="shares",
        xbrl=("us-gaap:EarningsPerShareDiluted", "us-gaap:EarningsPerShareBasic"),
    ),
    # --- balance-sheet (INSTANT) metrics — fiscal-year-end snapshots ---
    MetricConcept(
        concept_id="metric:Assets",
        pref_label="Total Assets",
        alt_labels=("assets", "total assets"),
        unit_class="currency",
        xbrl=("us-gaap:Assets",),
        period_type="instant",
    ),
    MetricConcept(
        concept_id="metric:Liabilities",
        pref_label="Total Liabilities",
        alt_labels=("liabilities", "total liabilities"),
        unit_class="currency",
        xbrl=("us-gaap:Liabilities",),
        period_type="instant",
    ),
    MetricConcept(
        concept_id="metric:StockholdersEquity",
        pref_label="Stockholders' Equity",
        alt_labels=("stockholders equity", "shareholders equity", "total equity",
                    "shareholders’ equity", "stockholders’ equity"),
        unit_class="currency",
        xbrl=("us-gaap:StockholdersEquity",
              "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        period_type="instant",
    ),
)


def default_registry() -> ConceptRegistry:
    return ConceptRegistry(DEFAULT_FINANCE_CONCEPTS)
