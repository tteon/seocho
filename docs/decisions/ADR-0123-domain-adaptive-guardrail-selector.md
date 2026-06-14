# ADR-0123: Domain-Adaptive Guardrail Selector

Date: 2026-06-14
Status: Proposed

## Context

ADR-0122 established there is **no single best guardrail**: a rich ontology
improves answers in entity/qualitative domains (Governance +0.67, Legal/Company
+0.42, Risk +0.33) but is neutral-to-harmful in numeric ones (Financials −0.08,
Shareholder return −0.17 even as conformance rose to 1.0). The team needs that
rule operationalized, not left as a finding.

## Decision

Add `seocho.guardrail_selector.select_guardrail(candidates, corpus_profile)` —
picks the best guardrail ontology for a corpus, domain-adaptively, offline:

1. Score each candidate against the corpus with the corpus-aware scorecard
   (`profile="guardrail"`, ADR-0116) → `corpus_coverage`.
2. Estimate the corpus's **numeric intensity** (frequency-weighted fraction of
   entity mentions that are metric/quantity-like).
3. Select: **entity** corpus (`numeric_intensity` low) → highest-coverage
   (richest adequate) candidate; **numeric** corpus → the **leanest** candidate
   within ε of best coverage (avoid over-enrichment noise), plus an advisory to
   apply numeric validation (P3/ADR-0119) instead of vocabulary enrichment.
   `select_per_domain()` maps the rule over per-domain profiles.

CLI: `seocho ontology select-guardrail --candidates lean=a.jsonld,rich=b.jsonld
--corpus profile.json`. Pure/offline (consumes a precomputed corpus profile, no
LLM, no hot path).

## Validation

`tests/seocho/test_guardrail_selector.py` (6): numeric_intensity discriminates
numeric vs entity corpora; entity corpus → rich; numeric corpus → lean + a
validation advisory; numeric corpus still picks rich when lean's coverage is far
worse (selector doesn't blindly force lean); per-domain mapping; profile loaders.

Real-data demo: on the FinDER open-extraction corpus profile (268 types,
`numeric_intensity` 0.33 → "entity") the selector picks **fibo_plus**
(coverage 0.35 < 0.49 < 0.63 for minus/base/plus), matching ADR-0118's overall
guardrail benefit. A Financials-only profile would push `numeric_intensity` up
and flip the choice to lean.

## Consequences

- The ADR-0122 finding is now an automatic, testable decision an operator (or a
  `seocho run`) can call to choose a guardrail per corpus/domain — rich where it
  helps, lean where enrichment hurts, with numeric domains routed to P3 validation.
- The numeric-intensity classifier is heuristic (keyword-based on the corpus's
  observed labels); refine with the answer-accuracy signal (ADR-0122) as that
  metric joins the eval surface.
- Follow-ups: wire selection into the e2e run-spec (per-domain guardrail); add
  answer-accuracy to the scorecard; learn the numeric-intensity threshold from
  measured per-domain deltas rather than a fixed 0.5.
