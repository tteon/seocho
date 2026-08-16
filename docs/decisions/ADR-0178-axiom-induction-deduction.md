# ADR-0178: inductive axiom mining + deductive entailment, A/B vs SHACL-only (seocho-ia4.8/9/10)

Date: 2026-08-16 · Status: accepted (offline mechanism measured; live e2e pending) · seocho-ia4.8/9/10

## Context

hadry's write-time-rigor thesis (Keet, induction/deduction §2.2.3): a KG earns its
keep by capturing as AXIOMS what the LLM missed, running validated inference, and
projecting an enriched graph. Worry: extracting axioms by hand is too cumbersome, and
SHACL-only over-depends on human-authored shapes. The indexing survey confirmed the
gap: the indexing path mines only single-*property* shape rules (`rules.py`,
required/datatype/enum/range) and has NO deductive/entailment step (owlready2 is
offline-only). The user: "이 실험을 한 번도 안 해봤다" — this A/B is net-new.

## Decision

`src/seocho/axioms.py`:
- **Induction** (`mine_axioms`) — mines functional / inverse-functional (from edge
  cardinality stats), disjointness (labels in the multi-label vocabulary that ~never
  co-occur, confidence-based so a rare violation doesn't suppress the axiom),
  subclass (A's extent ⊂ B's), and AMIE-lite composition rules R1∧R2⇒R3 — all with
  support + confidence. `approve()` = the cheap human-APPROVAL gate (keep high
  support+confidence), NOT authoring — this is the resolution to "cumbersome".
- **Deduction** (`materialize_entailments`) — applies approved axioms: subclass
  closure (ancestor labels) + composition rules (new edges), marks them
  `_entailed:"true"` (auditable, like `_out_of_ontology`), and DETECTS contradictions
  (functional / disjoint violations). Structural only; owlready2 stays offline.

Natural insertion point (from the survey): after the existing rule hook at
`pipeline.py:1002`, feeding the already-built per-document `graph_for_rules`, before
`_shape_and_write_graph` (so entailed edges inherit the provenance stamp).

## Result — A/B on a deterministic extracted-graph fixture (offline mechanism)

`scripts/agentos/ablation_axiom_ab.py` (70 nodes / 35 rels, planted patterns +
violations):

| metric | A: SHACL-only | B: induced+deduced |
|---|---|---|
| property-shape constraints | 13 | 13 |
| axiom classes mined (approved) | 0 | **12** (functional 4, inv-func 1, disjoint 5, subclass 1, rule 1) |
| contradictions caught | 0 | **2** (disjoint + functional violation) |
| entailed edges added | 0 | **1** (composition rule) |
| approval burden | — | **12 of 15** mined |

B catches 2 contradictions A structurally CANNOT (cross-type functional + disjoint),
and materializes an edge the LLM never asserted — at a quantified approval burden of
12 candidates. The "cumbersome" worry becomes a measured number, not a guess.

## Consequences

- The induction→deduction→projection pipeline is real and measured offline. `rules.py`
  (property shapes) + `axioms.py` (relational/logical axioms) are complementary.
- **Honest scope:** this measures the MECHANISM (axioms mined, contradictions caught,
  entailed structure, approval burden). It does NOT yet measure ANSWER QUALITY — does
  the enriched projection help the LLM answer better? That needs the live e2e (real
  dataset → API extraction → DozerDB), which is the pending run and the decision gate:
  adopt DL-as-shape (ia4.7) IFF the live answer-quality lift justifies the burden+cost;
  else record "SHACL sufficient". ia4.7 stays BLOCKED on ia4.10.
- 6 unit tests; new top-level module (0 regressions). Design: wiki/axiom-induction-
  deduction-projection-design.md.
