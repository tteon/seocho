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

## Live e2e result (real MARA extraction → DozerDB, 2026-08-16)

`scripts/agentos/e2e_axiom_ab.py`, MARA `MiniMax-M2.7`, dedicated DB `axiome2e`
(isolated from the finbench data on the same instance), finance-compliance corpus
(6 docs). End-to-end validated: real extraction → materialize → **corpus-scope**
mining (reads the whole assembled, interned graph from the store — the cross-chunk /
cross-document answer, not per-chunk).

- Extraction is REAL (ontology-typed: Company/Regulation/Regulator/Policy/
  ComplianceIncident/ControlEvidence), not the heuristic fallback (two credential
  bugs fixed en route: a quoted `MARA_API_KEY` sent verbatim → 401, and the model
  `MiniMax-M2.5` not on the new plan → swapped to `M2.7`).
- Whole graph: 30 nodes / 87 rels — but **24/30 are the memory-graph provenance
  layer** (Document/DocumentVersion/Chunk/Section) and most edges are plumbing
  (MENTIONS/HAS_CHUNK/HAS_VERSION). The miner must (and now does) scope to the
  DOMAIN layer, else it just rediscovers "each Document HAS_VERSION its versions".
- **Domain-scoped: 6 nodes / 7 rels → 2 trivial functional axioms, 0 disjoint/
  subclass/rule, 0 contradictions, 0 entailed.** The reason is decisive and honest:
  the 6 docs describe the SAME entities, so interning collapses them to **one
  instance per type** — there is no statistical mass for domain axioms; and this
  ontology is single-label (no multi-typing → disjoint/subclass cannot arise by
  construction).

**Finding:** the pipeline is correct and validated on real extraction, but induced
axioms need a corpus with **many instances per type** (many companies, regulations,
incidents) and, for disjoint/subclass, an ontology with multi-typing / a class
hierarchy. A 6-doc single-scenario sample is far below that threshold. The offline
fixture (above) already shows the mechanism fires when the data carries the patterns;
the live gap is corpus scale + type diversity, not the mechanism. Next: re-run on a
larger, instance-diverse corpus (e.g. FinDER / many filings) before judging the
answer-quality A/B and the DL-as-shape (ia4.7) decision.

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
