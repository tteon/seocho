# ADR-0114: Ontology Quality Scorecard — a Graded Measurement Instrument for TBox Health

Date: 2026-06-13
Status: Proposed

## Context

SEOCHO can already *define*, *serialize*, *lint*, *diff*, and *version* an ontology, and it
can score an *extracted graph* against one (`Ontology.score_extraction`). What it could not do
was answer the question an ontology engineer asks first: **"is this ontology any good, and
where is it weak?"** — before extraction runs, before a corpus exists.

The two adjacent surfaces do not cover this:

- `ontology_governance.build_ontology_governance_report` is a **binary promotion gate**
  ("safe to ship?"). It bundles check + context + artifact + SHACL + synthetic-sample
  validation and returns an `ok` boolean. It does not grade quality or rank what to fix.
- `Ontology.score_extraction` scores **ABox instances**, not the schema itself.

The pieces of an intrinsic TBox evaluation existed but were **scattered and un-graded**:
`lint_ontology` (hygiene), `competency_question_report` / `_coverage` (functional fit). There
was no taxonomy-health analysis at all (only cycle detection), and nothing composed these into
a single, comparable verdict. This is the "we haven't actually verified the ontology tooling
works" gap: with no scorecard, there is no way to know whether a schema edit (or a new version)
is an improvement.

## Decision

Add `seocho.ontology_scorecard.score_ontology(ontology, *, competency_questions=None,
weights=None) -> OntologyScorecard` — a graded, multi-dimensional quality scorecard. It is the
**measurement instrument** that anchors the broader ontology-management roadmap (refinement
loop, versioning-with-substance): you cannot improve what you cannot measure.

Five dimensions, each scored 0.0–1.0 with explicit, sorted `WeakPoint`s:

| Dimension | Source | What it measures |
|---|---|---|
| `structural_integrity` | composes `lint_ontology` | hygiene errors (blocking) + warnings |
| `taxonomy_health` | **new** | orphan (disconnected) classes, flatness, degenerate single-child branches, depth |
| `definitional_completeness` | new (uses `effective_identity_keys`) | definitions, identity coverage, bare-slot properties — the "under-specified?" signal |
| `constraint_richness` | new | typed relationship endpoints, declared cardinality, per-class constraints — "does it actually constrain extraction?" |
| `functional_coverage` | composes `competency_question_report` / `_coverage` | are the questions the ontology must answer expressible? (optional; skipped without CQs) |

Design rules:

- **Compose, do not duplicate.** Hygiene and competency analysis are reused from
  `ontology_governance`; the genuinely new contribution is the taxonomy-health tier plus the
  graded aggregation and weak-point ranking.
- **Offline, zero hot-path** (ADR-0043). Pure model walk; no LLM, no graph, no corpus required
  for the structural tiers. Lives in the data/governance plane.
- **Blocking caps the grade.** A hygiene *error* sets `blocking=True` and caps the letter grade
  at `D` regardless of the numeric score — a quantitatively pretty ontology that fails the
  linter must not read as shippable.
- **Abstract superclasses are exempt from identity and CQ-coverage.** A class that is a
  `broader` parent of another class *and* declares no properties of its own is treated as
  abstract (never directly instantiated, per OWL/OntoClean), so it is not penalised for lacking
  an identity key or for not being named by a competency question. This refinement came
  directly out of the validation experiment below.

Not exported from `seocho.__init__` — follows the existing offline-governance convention
(`ontology_governance` is imported directly), keeping it off the hot SDK surface.

## Validation (measured, before/after)

`scripts/benchmarks/ontology_scorecard_experiment.py` scores a realistic hand-written
first-draft ontology (the "before" a user actually produces) and the governed rewrite that
applies exactly the weak points the scorecard surfaced. Record:
`docs/decisions/ADR-0114-scorecard-experiment.json`.

| | overall | grade | weak points |
|---|---|---|---|
| **before** (first draft) | 0.554 | F | 13 |
| **after** (governed) | 0.918 | A | 2 |
| **Δ** | **+0.363** | F→A | **−11** |

Per-dimension lift: taxonomy_health +0.51, definitional_completeness +0.63, constraint_richness
+0.61, structural_integrity +0.08, functional_coverage −0.03 (adding classes the existing CQs
do not exercise — itself a true signal that the CQ set should grow with the schema).

Two findings honestly remained in the "after" arm — `Deal` still disconnected (a modeling gap
the author introduced and missed) and `Person` a single-child parent (an acceptable design
choice). The scorecard surfacing a residual defect the author overlooked is the point: it is a
guardrail, not a rubber stamp.

Cross-check against a known-good schema: the shipped `examples/run/schema.yaml` quickstart
scores **0.978 / A** with zero weak points, and an attempt to "improve" it by bolting on an
identity-less abstract root *lowered* the score — which is what prompted the abstract-class
exemption above. The tool refuses to reward gratuitous abstraction.

## Consequences

- A single, comparable number + actionable weak-point list to gate ontology edits and version
  bumps, and to drive the (future) refinement loop and versioning-with-substance layers.
- The taxonomy-health tier is intentionally conservative (flatness only flagged at ≥6 classes;
  cardinality weighted lightly). Deeper taxonomy verification — OntoClean rigidity/identity
  meta-property checks via owlready2, and LLM-assisted CQ generation — are deferred follow-ups
  (tickets off `seocho-g2r`), to be added as their own scored tiers.
- A `seocho ontology score` CLI subcommand is a natural follow-up (not in this change).
