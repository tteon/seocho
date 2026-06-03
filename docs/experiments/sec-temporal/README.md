# SEC temporal / prior-resistance benchmark — graph contribution, finally measured

## Why this exists

Every answer-path leg this session (AnswerShape, RouteProfile, F8 multi-plan,
scored grounding) measured **null** on FinDER. The cold review and the
cross-model judge pinned the cause: **prior-masking**. FinDER's famous-company
subset (Apple HQ, FY2022 revenue) is memorised by the LLM, so the judge scored
the answer 0.70 whether or not the graph contributed. We had no instrument that
could isolate the graph's contribution.

This benchmark removes the prior. It pulls **recent SEC 10-K facts** straight
from EDGAR XBRL `companyfacts` and asks for exact line items — including
**FY2025**, whose period ends after the model's training cutoff. For those
facts the model *cannot* know the answer, so any correct grounded answer is
unambiguously the graph contributing.

- Generator: `scripts/benchmarks/sec_temporal_bench.py` (deterministic XBRL
  gold, fiscal-year labels from the annual `frame`, `prior_stale` flag for
  post-cutoff years).
- Runner: `scripts/benchmarks/sec_temporal_run.py` (closed-book vs grounded,
  scale-aware numeric matching, the three A/Bs).

## Dataset

20-company large-cap basket × FY2023/2024/2025, two duration metrics (revenue,
net income): **102 questions, 34 prior-stale (FY2025), 34 (ticker, metric)
groups**. Each group's corpus carries all three years' fact sentences, so a
question must disambiguate both the metric and the year.

## Results (n=102, MARA / MiniMax-M2.5, DozerDB, chunk fallback ON)

| A/B | closed-book (prior only) | grounded (graph) |
|-----|--------------------------|------------------|
| **overall accuracy** | 0.412 | **1.00** |
| **prior-stale FY2025** (n=34) | **0.059** | **1.00** |
| fresh FY2023/24 (n=68) | 0.588 | 1.00 |
| **temporal resolution** | — | **102/102 correct, 0 wrong-year, 0 no-match** |

The graph **fixed 32/34 prior-stale cases** the model got wrong. Grounded misses: 0.

### What the three hypotheses returned

- **H1 — prior-staleness correction.** On FY2025 the prior is right 5.9% of the
  time (it mostly, and correctly, refuses: *"FY2025 has not yet been
  reported"*); grounded is right 100%. **Confirmed** — this is the clean,
  prior-independent graph-contribution signal FinDER could never produce.
- **H2 — graph contribution.** Grounded beats closed-book overall (0.41 → 1.00).
  On the prior-stale slice the gap is the whole story (0.06 → 1.00). **Confirmed.**
- **H3 — temporal resolution.** Every grounded answer returned the *asked*
  year's value, never another year present in the same corpus (102/102, 0
  wrong-year). **Confirmed.**

### The chunk fallback is the mechanism

On these short fact sentences the structured Cypher lane returns **empty**; the
graph answers via the **graph-native chunk fallback** (`SEOCHO_CHUNK_FALLBACK`,
commit `1e131ac`). Grounded answers explicitly cite *"additional context from
vector search"* — the fallback retrieving the FY-specific sentence the
structured lane missed. This benchmark is the e2e validation that leg was
missing.

## Honest scope — what this proves and what it does not

- **Proves:** SEOCHO's store → retrieval → synthesis pipeline correctly keeps
  and retrieves temporally-distinct facts, resolves the asked year with zero
  cross-year confusion, and is the *only* source of truth where the model's
  prior is stale.
- **Does NOT prove robustness on noisy input.** The grounded corpus is XBRL
  values rendered as clean fact sentences, so grounded = 1.00 is a **ceiling**:
  the answer was handed to the indexer in one sentence and the chunk fallback
  retrieved it. The headline result is therefore the **closed-book↔grounded
  delta on prior-stale facts** and the **temporal-resolution rate**, NOT "the
  graph is 100% accurate." (Reporting it as a quality win would repeat the
  AnswerShape token-F1 over-claim this session already corrected.)
- **Next step (documented follow-up):** index the actual 10-K MD&A *narrative*
  text instead of synthesized sentences. That exercises real extraction and
  retrieval noise — where the structured lane, chunk fallback, and grounding
  legs face a genuine test and the ceiling will move off 1.00.

## Reproduce

```bash
# dataset (network: EDGAR)
python scripts/benchmarks/sec_temporal_bench.py --years 3 --cutoff-year 2024 \
  --out outputs/evaluation/sec_temporal/dataset.jsonl

# run (network: EDGAR-free; needs DozerDB + MARA_API_KEY)
SEOCHO_CHUNK_FALLBACK=1 PYTHONPATH=src:extraction \
  python scripts/benchmarks/sec_temporal_run.py \
  --out outputs/evaluation/sec_temporal/run_full.json
```

Dataset and full run JSON stay local-only (`outputs/` gitignored) per
`docs/BENCHMARKS.md`; the aggregate + the 34 stale records are committed in
`results_summary.json` for reproducibility of the headline numbers.
