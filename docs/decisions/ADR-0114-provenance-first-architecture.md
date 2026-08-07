# ADR-0114: Provenance-first extraction, ontology-as-validator, and evidence-conditional evaluation

Date: 2026-08-08 · Status: accepted · Tracking: bead hq-ygj (epic, four bundles)

## Context

The LoG 2026 study (papers/log2026/anchor/, ledger papers/log2026/PREREGISTRATION.md,
33 registered verdicts, artifacts indexed in experiments/results_index.json)
measured the assumptions SEOCHO's pipeline was built on. Several failed, and
the failures are replicated and quantified.

## Decisions

1. **Provenance-first ingestion.** Every extracted figure is anchored to its
   source token at write time (`_anchor_passage/_anchor_offset/_anchor_verified`
   become pipeline-native, not post-hoc). Basis: 26.7% of anchored values are
   unit-scale misreads; post-hoc recovery caps at 72–80%.
2. **Alignment keys on provenance, not names.** The linker treats source
   coordinates as the primary dedup/verification key for figure-bearing
   nodes. Basis: name matching hides 80–84% of cross-extractor disagreements;
   all 1,482 measured value conflicts are unit-scale splits.
3. **Ontology role defaults to validator.** `ontology_role: validate|guide|both`,
   default `validate`: minimal schema in extraction prompts; the ontology runs
   post-hoc (rules/SHACL) and supplies serving-time type labels. Basis: guidance
   lowered cross-model agreement and content coverage at 280 cases; the only
   positive effects were violation detectability and serving-time legibility.
4. **Provenance-decorated serialization.** The graph-as-context serializer
   ships typed labels, `source=[p@offset]` decoration, a cite instruction, and
   plumbing-label exclusion. Basis: pointer-only decoration raised accuracy
   (+0.05, two of three models) and repaired over-refusal (41→26).
5. **Evidence-conditional evaluation.** Eval reports grounded-correct /
   contaminated / honest-abstention / over-refusal, never gold-overlap alone.
   Basis: naive scoring rewarded memorization and punished honest abstention;
   the decomposition reversed condition orderings per stratum.
6. **Pre-flight profile.** A `seocho profile` diagnostic computes the
   verifiability profile (V, name-blind disagreement, scale fragility,
   guardrail surface, per-model legibility, anchored structural divergence)
   from one pilot extraction before multi-agent or ontology spend. Reported as
   diagnostic, not predictor — its registered predictive form failed its first
   prospective test (two of three models).

## Consequences

Owlready2 stays offline (§6.3, unchanged). The extraction hot path gains one
deterministic verification step per figure (string search, no LLM). Ontology
prompt budgets become a lintable quantity. Evaluation output schema changes
(additive). Implementation is tracked under bead hq-ygj; nothing in this ADR
alters runtime contracts until those land.
