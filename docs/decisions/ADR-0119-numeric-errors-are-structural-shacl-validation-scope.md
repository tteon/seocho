# ADR-0119: Financial Numeric Errors Are Mostly *Structural*, Not Arithmetic — SHACL Validation Has High Recall, Needs Precision Engineering

Date: 2026-06-14
Status: Proposed

## Context

ADR-0118 scoped SEOCHO's numeric correctness claim to **validation, not computation**, on the
premise (from the survey) that "ontology grounding doesn't do arithmetic." Before building numeric
validation we ran P3 to measure the actual shape of LLM financial-numeric error: **what fraction is
structurally catchable by constraint validation vs inherently arithmetic, and how well does
SEOCHO's real `validate_with_shacl` catch it?**

## Experiment

`scripts/benchmarks/p3_shacl_numeric_validation.py`, MARA DeepSeek-V3.1, **80 FinDER
numeric-reasoning cases** (16 each × Compositional/Division/Multiplication/Subtract/Addition).
Per case: extract numeric facts + answer → validate facts with SEOCHO's real
`Ontology.validate_with_shacl` + a numeric-rule supplement → LLM-judge correctness vs gold →
LLM-judge error type (structural vs arithmetic) for wrong answers. Record:
`ADR-0119-p3-shacl-numeric-validation.json`; summary:
`ADR-0119-p3-summary.json`.

## Findings (measured)

| metric | value |
|---|---|
| numeric accuracy | **0.56** (45/80) — matches survey (~50-60%) |
| wrong answers | 35 |
| **% wrong that are STRUCTURAL** (bad/missing/mis-typed extracted fact) | **91%** (32/35) |
| % wrong that are ARITHMETIC (facts ok, math wrong) | **9%** (3/35) |
| **validator catch-rate on structural-wrong** | **0.94** |
| validator false-positive rate on *correct* answers | **0.91** |

By-type accuracy: Multiplication 0.38 (worst), Subtract 0.56, Compositional/Division/Addition 0.62.

## Interpretation — this refines ADR-0118 upward

1. **The dominant financial-numeric failure is grounding, not arithmetic.** Only **9%** of wrong
   answers were pure calculation errors; **91%** were *structural* — the model pulled the wrong
   number, wrong period, wrong unit/scale, or wrong company. That is exactly what ontology +
   constraint governance addresses. So the ADR-0118 framing ("ontology doesn't do arithmetic" —
   still true) **understated** the value: arithmetic is the small slice; the large slice is
   governable. SEOCHO's numeric play is therefore broader than "catch the rare math slip."
2. **Structural validation has high recall (0.94) but poor precision (0.91 FP).** As a binary gate
   it is currently useless — it flags almost everything, including correct answers. Root cause is
   partly **naive constraint design**: required-`period`/`value`-as-float is too strict for messy
   financial extraction, and rigid `unit`/`scale` enums mis-fire when the model conflates them
   (e.g. it put "millions" in `unit`). The precision problem is an engineering problem, not a
   fundamental limit.

## Decision

- **Keep numeric validation in scope and treat it as a primary value driver** (not a footnote):
  most numeric errors are structurally detectable. Build P3 numeric-fact validation
  (unit/scale/period/reconciliation/materiality, cross-entity) as a first-class feature.
- **Engineer for precision, not just recall.** (a) Validate the *answer-relevant* fact, not every
  extracted fact; (b) relax presence constraints (don't require `period` to merely flag);
  (c) normalize unit/scale before enum checks (the conflation is the model's, fix it in mapping);
  (d) use validation as a **soft signal / confidence + repair trigger**, ranking and re-asking,
  rather than a hard reject. Re-measure precision/recall after tuning.
- **Reconciliation is the highest-value check** (sum-of-parts = total, period consistency) because
  it catches wrong-number-pulled without recomputing the answer — pursue it first.

## Consequences

- Answers the survey's open question ("no source measured a KG-grounding intervention's effect on
  numeric errors"): structural validation catches 94% of the structural errors that constitute 91%
  of numeric failures — but precision must be earned.
- Feeds the ambiguity-review loop (seocho-2mg): unit/scale conflation and missing-period are
  exactly the structural signals that should quarantine a fact for review.
- Tickets: new P3 numeric-validation feature ticket; precision-tuning as its first milestone.
