# ADR-0131: Source-Grounded Numeric Check — Recovers Real Recall, Precision Needs Derived-Fact Filtering

Date: 2026-06-14
Status: Proposed

## Context

ADR-0130 concluded that isolated-fact numeric rules cannot catch the dominant
financial error ("wrong number pulled" — a plausible-but-wrong value), and that
recall needs **source grounding**: check whether an extracted value actually
appears in the source text. This ADR implements and measures that.

## Decision

Add source grounding to `seocho.numeric_validation`:

- `extract_source_numbers(text)` — all numbers in the source (commas, `$`, `%`,
  parenthesised negatives, and scale-expanded forms: `$539.2 million` → 539.2 and
  539_200_000).
- `ground_facts(facts, source_text, *, rel_tol)` — flags each extracted value with
  no source match as `warn` (`ungrounded_value`), scale-aware.
- `validate_numeric_facts(facts, *, source_text=None)` folds grounding in when a
  source is supplied (precision preserved when it is not).

## Validation (measured, P3 v3, MARA, 80 FinDER numeric cases)

`scripts/benchmarks/p3v3_grounded_validator.py`, record
`docs/decisions/ADR-0131-p3v3-grounded.json`. Same protocol as P3 v1/v2; three
validators on the extracted facts.

| validator | recall on structural-wrong | false-positive on correct |
|---|---|---|
| OLD (SHACL + rigid rules) | 0.94 | 0.91 |
| NEW (isolated-fact soft) | 0.00 | 0.00 |
| **GROUNDED (value ∈ source)** | **0.22** | **0.51** |

(45 correct, 18 structural-wrong.)

## Interpretation — directionally right, precision work remains

1. **Grounding is the only approach that recovers non-trivial recall on a real
   precision budget.** It catches ~22% of structural errors — the fabricated /
   mis-computed values absent from the source — which both isolated-fact rules
   (NEW: 0) and the flag-everything OLD (recall is an artifact of 0.91 FP) cannot
   meaningfully detect.
2. **But it over-flags correct answers (FP 0.51).** Root cause: it grounds *all*
   extracted facts, including **derived / intermediate values** the model computes
   (a ratio, a difference, the answer itself) that are legitimately absent from
   the source — plus number-format mismatches the extractor/matcher misses.
3. So grounding-as-is is a useful *signal* but not yet a usable gate. The honest
   recall ceiling is also bounded: a wrong value that happens to equal a *different*
   real source number (wrong-cell selection) is grounded and uncatchable here.

## Decision / next

- Ship `ground_facts` + `validate_numeric_facts(source_text=)` as a soft signal /
  repair-trigger (it is the best numeric-error detector measured so far), but
  **do not gate on it** at FP 0.51.
- To make it a gate, the next steps (measured before claiming): (a) **distinguish
  source-quoted facts from derived ones** — have the extractor mark whether each
  fact is quoted vs computed, and ground only the quoted ones; (b) harden number
  normalization/matching (currency words, ranges, units); (c) consider grounding
  only the *answer-relevant input* facts.
- Synthesis across ADR-0119/0130/0131: numeric correctness is genuinely hard;
  flag-everything and flag-nothing both fail; grounding is the right lever and the
  remaining work is derived-fact filtering, not more isolated-fact rules.

## Consequences

- SEOCHO now has a measured numeric-error *detector* (grounding) with known
  recall/precision, recorded honestly rather than asserted.
- This is the financial-numbers-matter throughline (the user's emphasis): the
  product's numeric guarantee is source-grounded validation, scoped to what the
  measurements show it can do.
