# ADR-0130: Measured — Isolated-Fact Numeric Validation Cannot Catch "Wrong Number Pulled"; Recall Needs Source-Grounded Reconciliation

Date: 2026-06-14
Status: Proposed

## Context

ADR-0127 redesigned numeric validation to be precision-first (soft findings,
unit/scale normalization, relaxed presence, reconciliation) to fix ADR-0119's
measured precision failure (old validator: recall 0.94 on structural-wrong cases,
but **91% false-positive on correct answers** — it flagged almost everything). We
re-measured (P3 v2) to confirm the new validator keeps recall while fixing
precision.

## Experiment

`scripts/benchmarks/p3v2_validator_precision.py`, MARA DeepSeek-V3.1 (ub5 layer),
**80 FinDER numeric-reasoning cases**, workers=6 + 429-retry. Extract facts +
answer → judge correctness + error type → apply BOTH validators to each case's
facts. Record: `docs/decisions/ADR-0127-p3v2-validator-precision.json`.

## Findings (measured)

| validator | recall on structural-wrong | false-positive on correct |
|---|---|---|
| OLD (ADR-0119: SHACL + rigid enums + required period) | **0.93** | **0.91** |
| NEW (ADR-0127: soft + normalized + reconciliation) | **0.00** | **0.045** |

(80 scored: 44 correct, 28 structural-wrong, 8 arithmetic/other.)

## Interpretation — an honest negative result

1. **The new validator achieved precision (FP 0.91 → 0.045) but recall collapsed
   to 0.0.** By relaxing missing-period/unknown-unit to `info` and only `warn`-ing
   on non-numeric value / implausible sign / failed reconciliation, it warns on
   almost nothing — including the structural errors.
2. **The old validator's 0.93 recall was an artifact of flagging *everything***
   (91% of correct answers too). A detector that fires on ~all inputs has no real
   recall. So neither extreme is a usable guardrail.
3. **The dominant structural error is "wrong number pulled"** — a *plausible*
   value that happens to be wrong (correct type, unit, period; just not the right
   figure). **No isolated-fact rule can detect this**, because nothing in the
   extracted fact alone reveals it is wrong. Reconciliation is the right idea, but
   FinDER extractions rarely yield clean part/total groups, so it almost never
   fires here.

## Decision

- **Keep `numeric_validation` as a precision-first SOFT signal** for the genuinely
  catchable subset (non-numeric value, implausible sign, and reconciliation *when
  groups exist*) and as a repair-trigger — but **do not claim it detects
  wrong-number-pulled.** ADR-0127's "high recall" expectation is corrected here.
- **Recall on the dominant error requires source-grounding**, not richer
  isolated-fact rules: (a) compare each extracted value against the numbers
  actually present in the source span (the extractor should emit the supporting
  span/evidence), and/or (b) reconstruct reconciliation groups from the source
  table. This is the real next feature for numeric correctness.

## Consequences

- Honest scoping: SEOCHO's numeric guarantee is **validation of the catchable +
  source-grounded reconciliation**, not blanket numeric error detection. This
  sharpens ADR-0118/0119's "scope numerics to validation" with what validation
  can and cannot do.
- Follow-up: an evidence-grounded numeric check (extracted value ⊂ source-span
  numbers; table reconciliation) — measure its recall/precision before claiming
  numeric-error detection.
- Negative results are recorded deliberately: a validator that flags everything
  (old) or nothing (new) both fail; the path forward is grounding, not tuning
  isolated-fact thresholds.
