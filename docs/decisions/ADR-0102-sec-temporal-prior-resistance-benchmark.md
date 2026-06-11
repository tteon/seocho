# ADR-0102: SEC Temporal / Prior-Resistance Benchmark

Date: 2026-06-03
Status: Proposed

## Context

The opik-derived answer-path legs (ADR-0109 AnswerShape/RouteProfile, ADR-0100
F8 multi-plan, ADR-0101 scored grounding) all measured **null** on FinDER, and
the cross-model judge showed AnswerShape's token-F1 "win" was a metric artifact
(judge 0.70 → 0.70). The diagnosed root cause was **prior-masking**: FinDER's
famous-company subset is memorised by the LLM, so the model answers correctly
from priors whether or not the graph retrieved anything. No instrument in the
session could isolate whether the graph layer *contributes*.

FinDER originates from SEC 10-K filings. Pulling **recent** filings — especially
fiscal years whose period ends after the model's training cutoff — produces
facts the prior provably cannot know, which is exactly the prior-resistant
workload needed to measure graph contribution cleanly. SEC EDGAR's XBRL
`companyfacts` API gives deterministic, fiscal-year-labelled gold values with no
key and no LLM-extraction circularity.

## Decision

Add a benchmark track (generator + runner + pure-logic unit tests) that:

1. **Generates** (`scripts/benchmarks/sec_temporal_bench.py`) a temporally
   labelled dataset from EDGAR XBRL: deterministic gold = fact value; fiscal
   year from the annual `frame` (`CY2024`), merged across migrated concept tags
   (`Revenues` → `RevenueFromContractWithCustomer...`) with a recent-year window
   to drop non-standard-tag noise; a `prior_stale` flag for post-cutoff years.
   Rows reuse the `graphrag_bench` JSONL shape plus temporal fields.
2. **Runs** (`scripts/benchmarks/sec_temporal_run.py`) three A/Bs per question:
   closed-book (LLM prior only) vs grounded (corpus indexed → `ask()`),
   prior-staleness correction (the same two accuracies on the FY2025 slice), and
   temporal resolution (did the grounded answer return the *asked* year's value,
   not another year present in the same corpus). Numeric matching is scale-aware
   (`$416,161 million` ≡ `$416.2 billion` ≡ `416161000000`).

Default-off / opt-in nature: this is a benchmark, not runtime behavior — it
touches only `scripts/benchmarks/`, no `src/seocho/` or `runtime/` surface.

## Evidence (n=102, MARA/MiniMax-M2.5, DozerDB, `SEOCHO_CHUNK_FALLBACK=1`)

20-company basket × FY2023/24/25 × {revenue, net income}; 34 prior-stale.

| A/B | closed-book | grounded |
|-----|-------------|----------|
| overall | 0.412 | 1.00 |
| **prior-stale FY2025** | **0.059** | **1.00** |
| fresh FY2023/24 | 0.588 | 1.00 |
| temporal resolution | — | 102/102 correct, 0 wrong-year |

Graph fixed 32/34 prior-stale cases; grounded misses 0. All three hypotheses
(prior-staleness, graph contribution, temporal resolution) confirmed.

## Consequences

Positive:

- **First clean, prior-independent measurement of graph contribution this
  session.** On post-cutoff facts the prior honestly refuses (*"FY2025 has not
  been reported"*) while the graph supplies the exact figure — the signal
  FinDER's prior-masking made unmeasurable.
- **E2E validation of the chunk fallback** (`1e131ac`). Structured Cypher
  returns empty on these short fact sentences; the graph answers via the
  graph-native chunk fallback, which grounded answers explicitly cite. This is
  the missing e2e for that leg.
- A reusable, deterministic, prior-resistant benchmark asset answering the cold
  review's standing demand for a non-synthetic / prior-resistant corpus.

Honest limits (explicitly, to avoid repeating the AnswerShape over-claim):

- The grounded corpus is XBRL values rendered as **clean fact sentences**, so
  grounded = 1.00 is a **ceiling** (the answer is handed to the indexer in one
  sentence and the fallback retrieves it). The defensible headline is the
  **closed-book↔grounded delta on the prior-stale slice** and the
  **temporal-resolution rate** — NOT an absolute accuracy claim.
- The two prior-stale cases the prior also "got right" (CSCO, INTC) are
  borderline-cutoff fiscal years (FY ends mid-2025) where the model had
  approximate data or matched within rounding tolerance — not evidence the
  prior knows FY2025.

Deferred / follow-up:

- Index the real 10-K MD&A **narrative** text instead of synthesized sentences,
  to exercise genuine extraction + retrieval noise (where the ceiling moves off
  1.00 and the structured / fallback / grounding legs face a real test).
- Add balance-sheet (instant-frame) metrics with fiscal-year-end-aware frame
  handling (current generator covers duration metrics: revenue, net income).
- Cross-model LLM-judge scoring on this set (the value-match scorer is
  deterministic; a judge pass would catch partial/hedged answers).

## Implementation Notes

- new: `scripts/benchmarks/sec_temporal_bench.py` (commit `5ac74cc`),
  `scripts/benchmarks/sec_temporal_run.py` (commit `39d16dc`).
- tests: `scripts/benchmarks/test_sec_temporal_bench.py` (9),
  `scripts/benchmarks/test_sec_temporal_run.py` (6) — all network/LLM-free.
- artifacts: dataset + full run JSON stay local-only (`outputs/` gitignored,
  `docs/BENCHMARKS.md`); aggregate + 34 stale records committed at
  `docs/experiments/sec-temporal/results_summary.json`.
- relates to: ADR-0109 (the prior-masking correction that motivated this),
  ADR-0101 (grounding's null FinDER result), and the chunk fallback `1e131ac`
  (the mechanism this benchmark validates e2e).
