# ADR-0127: Precision-First, Soft Numeric-Fact Validation (Reconciliation)

Date: 2026-06-14
Status: Proposed

## Context

ADR-0119's P3 experiment: financial numeric errors are ~91% *structural*, and a
constraint validator caught ~94% of them (recall) but flagged ~91% of *correct*
answers too (precision) — because rigid unit/scale enums mis-fire (the model puts
"millions" in `unit`) and a required `period` is too strict. ADR-0119's decision
was to engineer for precision: validate softly, normalize unit/scale, relax
presence, and prioritize reconciliation.

## Decision

New pure/offline module `seocho.numeric_validation`:

- `NumericFact.from_dict` — tolerant parse; **a scale word found in the `unit`
  field (e.g. "millions") is recognised as a scale and relocated**, not flagged
  (the dominant false-positive source in ADR-0119).
- Findings are **soft only** (`info` / `warn`, never a hard reject). Missing
  period → `info` (relaxed); unknown unit after normalization → not a warn;
  non-numeric value, implausible sign, and failed reconciliation → `warn`.
- `validate_numeric_facts(facts)` → findings + a `confidence` (drops only on
  `warn`) + `repairs` (soft re-ask suggestions).
- `reconcile(parts, total, rel_tol)` and a heuristic `find_reconciliation_groups`
  — sum-of-parts ≈ total catches a wrong number pulled without recomputing.

The intent vs ADR-0119: clean facts are **not** flagged (high precision), while
the structural errors that matter (bad number, broken reconciliation) still warn.

## Validation

`tests/seocho/test_numeric_validation.py` (7): a well-formed fact set →
confidence 1.0, zero warnings (the precision fix — contrast ADR-0119's 91% FP);
`unit="millions"` normalized to scale, not flagged; non-numeric value warns;
missing period is `info` (confidence stays 1.0); negative revenue warns;
reconciliation passes on 3+4=7 and warns on 3+4≠10 (unit + end-to-end). `run_basic_ci` green.

## Consequences

- A usable numeric guardrail for the numeric domains where vocabulary enrichment
  does not help (ADR-0122): soft signals + reconciliation + a confidence the
  query lane can use as a repair trigger, rather than a hard reject that flags
  everything.
- Follow-ups: re-run the P3 measurement (FinDER numeric cases) with this validator
  to quantify the new precision/recall; wire `confidence` into the query/repair
  loop; pass explicit reconciliation groups when the extractor knows them.
