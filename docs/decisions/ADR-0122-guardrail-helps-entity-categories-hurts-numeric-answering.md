# ADR-0122: Across All FinDER Case Types, the Guardrail Helps *Entity* Categories' Answers and Is Neutral-to-Harmful for *Numeric* Ones → Domain-Adaptive Guardrail Selection

Date: 2026-06-14
Status: Proposed

## Context

Prior runs measured extraction *conformance* (ADR-0115/0118) and numeric-fact
*validation* (ADR-0119). The open question for "check all the cases": does the
ontology guardrail improve the thing users want — **correct answers** — across the
full FinDER case space (8 categories; reasoning lives only in Company overview +
Financials)? This run builds the complete category × arm **answer-accuracy**
matrix.

## Experiment

`scripts/benchmarks/finder_guardrail_answer_matrix.py`, MARA DeepSeek-V3.1 via the
ub5 structured-output layer (ADR-0120). **96 cases (12 × 8 categories)**, each
answered twice — sparse `fibo_minus` vs rich `fibo_plus` injected as the guardrail
— then LLM-judged for correctness vs FinDER gold. Re-run at 6 workers with
429-retry after a first run was contaminated by MARA rate-limits (honest fix).
**0 errors, n=12 every category.** Record:
`docs/decisions/ADR-0122-finder-answer-matrix.json`.

## Findings (measured)

Overall answer accuracy: **sparse 0.458 → rich 0.667 (+0.21).**
By kind: lookup 0.464→0.667 (+0.20); reasoning 0.417→0.667 (+0.25).

| category | acc sparse | acc rich | Δ acc | conformance A→B |
|---|---|---|---|---|
| **Governance** | 0.25 | **0.92** | **+0.67** | 0.08→0.83 |
| **Company overview** | 0.42 | **0.83** | **+0.42** | 0.55→0.88 |
| **Legal** | 0.42 | **0.83** | **+0.42** | 0.58→0.98 |
| **Risk** | 0.25 | **0.58** | **+0.33** | 0.42→0.97 |
| Footnotes | 0.67 | 0.75 | +0.08 | 0.81→1.0 |
| Accounting | 0.42 | 0.42 | **0.00** | 0.69→0.96 |
| Financials | 0.67 | 0.58 | **−0.08** | 0.86→0.75 |
| Shareholder return | 0.58 | 0.42 | **−0.17** | 0.96→1.0 |

## Interpretation

1. **The guardrail's answer-accuracy value is real and large for entity/qualitative
   categories** — Governance +0.67, Company overview +0.42, Legal +0.42, Risk
   +0.33. Here the rich vocabulary lets the model represent the people, committees,
   regulations, and risks the questions are about, so it answers them.
2. **For numeric/metric-heavy categories the guardrail is neutral-to-harmful** —
   Accounting 0.00, Financials −0.08, **Shareholder return −0.17**. The sharpest
   case: Shareholder return conformance rose 0.96→1.0 yet answer accuracy *fell*
   0.58→0.42. **More conformant extraction did not produce better answers — the
   richer ontology's extra entity types are a distraction when the task is
   arithmetic over a few metrics.** This is the same structural-not-arithmetic
   boundary as ADR-0118/0119, now visible at the answer level across all cases.
3. So the guardrail is not universally good: it is a **domain-conditional** tool.
   Blanket enrichment hurts the numeric tail.

## Decision (develop accordingly)

- **Domain-adaptive guardrail selection.** Do not ship one ontology for all
  categories. Choose the guardrail per domain/corpus using the corpus-aware
  scorecard (ADR-0116, `profile="guardrail"`): rich for entity-heavy domains,
  lean for numeric domains. This is a concrete feature: a per-domain guardrail
  resolver keyed by corpus_coverage + the measured answer-accuracy deltas here.
- **For numeric domains, lean on numeric VALIDATION (P3/ADR-0119), not vocabulary
  enrichment.** Reconciliation / unit / period checks, not more entity types.
- **Add answer-accuracy (not just conformance) to the evaluation surface** — this
  run shows conformance and answer accuracy can move in *opposite* directions
  (Shareholder return), so conformance alone is an unsafe proxy.

## Consequences

- The product claim is now precise and honest across the full case space: "an
  ontology guardrail materially improves answers in entity/qualitative financial
  domains (Governance/Legal/Company/Risk: +0.3 to +0.7) and should be applied
  selectively — for numeric domains, structural validation, not vocabulary, is the
  lever."
- Feeds: domain-adaptive guardrail selection (new follow-up on seocho-g2r);
  answer-accuracy metric in the scorecard/eval; P3 precision work (ADR-0119).
- Caveat: single model (DeepSeek-V3.1), n=12/category, LLM-judge correctness;
  directional and consistent with three prior runs, but widen N + add a second
  judge before headline external claims.
