# ADR-0154: Provenance-first extraction, ontology-as-validator, and evidence-conditional evaluation

Date: 2026-08-08 · Status: accepted

## Context

The LoG 2026 study (research line `feat/ontology-workbench-gap-closure`;
submission "Anchor, Don't Name", OpenReview #145; 33 pre-registered
hypotheses, all verdicts on file with frozen artifacts) measured assumptions
SEOCHO's pipeline was built on. Several failed, replicated and quantified:

- 26.7% of anchored extracted values are unit-scale misreads; all 1,482
  measured cross-extractor value conflicts are unit-scale splits.
- Name-based fact matching hides 80–84% of cross-extractor disagreements;
  provenance-anchored alignment exposes 1.7–2.8x as many comparable facts.
- Ontology guidance in the extraction prompt lowered cross-model name
  agreement and answer-relevant coverage at 280 cases; its only measured
  positive effects are violation detectability and serving-time type
  legibility.
- Naive gold-overlap scoring cannot separate evidence use from memorization;
  an evidence-conditional decomposition reverses condition orderings per
  question stratum.
- Pointer-only provenance decoration (`source=[p@offset]`, no text) raised
  answer accuracy on two of three models and repaired over-refusal.

## Decisions

1. **Provenance-first ingestion**: extracted figures are anchored to their
   source token at write time; anchor fields become pipeline-native.
2. **Alignment keys on provenance, not names**: the linker treats source
   coordinates as the primary dedup/verification key for figure-bearing
   nodes.
3. **Ontology role defaults to validator**: `ontology_role: validate|guide|both`,
   default `validate` — minimal schema in extraction prompts; ontology runs
   post-hoc (rules/SHACL) and supplies serving-time type labels. Owlready2
   stays offline-only (unchanged).
4. **Provenance-decorated serialization**: the graph-as-context serializer
   ships type labels, source pointers, a cite instruction, and
   document-plumbing exclusion.
5. **Evidence-conditional evaluation**: eval reports grounded-correct /
   contaminated / honest-abstention / over-refusal, never gold overlap alone.
6. **Pre-flight verifiability profile**: a diagnostic computes, from one
   pilot extraction, the shares that gate multi-agent and ontology-guardrail
   spend. Diagnostic, not predictor — its registered predictive form failed
   its first prospective test and is reported as such.

## Consequences

The extraction hot path gains one deterministic per-figure verification
(string search, no LLM). Ontology prompt budgets become lintable. Evaluation
output schema changes additively. Implementation is tracked in the internal
tracker (epic: provenance-first product reflection); nothing changes runtime
contracts until those land.
