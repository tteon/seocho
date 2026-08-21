# ADR-0179: cold-start schema bootstrap — upper-ontology-anchored open extraction

Date: 2026-08-16 · Status: accepted (design + core; live extraction mode WIP) · seocho-ia4 (cold-start)

## Context

At cold-start (no domain ontology) SEOCHO does flat open LLM extraction with empty
type slots, so the LLM invents a different type name for the same concept per chunk
(vocabulary drift) — fragmenting the graph and starving axiom induction (a root cause
of the thin live e2e in ADR-0178). hadry's design principle: *domain-driven
interfaces let you think abstractly, without hyperfixating on dataset quirks* — so the
schema we design against must be an ABSTRACT domain interface, not the dataset's
concrete types.

## Decision

**Upper-ontology-anchored open extraction + post-pass induction.** We design/fix a
small **upper (foundational) ontology** as the abstract interface; concrete types are
emergent but **anchored** to an upper category, so synonyms cluster and the schema
self-assembles under a stable frame.

- `src/seocho/ontology/upper.py` — a small (~11 category) foundational ontology
  (Agent/Organization/Person/Artifact/Event/Concept/Location/TimeInterval/Claim/
  Quantity/Document, with Person/Organization ⊑ Agent) + abstract relations
  (partOf/participatesIn/causes/attributedTo/locatedIn/governs/precedes/hasValue).
  Deliberately small + abstract = a *soft frame*, not a rich closed vocabulary
  (avoids the "Anchor, Don't Name" / firewall recall hit). `render_upper_frame()`
  emits the extraction-prompt frame + anchoring instruction.
- `src/seocho/ontology/induce.py::induce_ontology_from_graph` — post-pass: turns an
  upper-anchored graph into a domain `Ontology` (concrete types as `NodeDef`s with
  `broader=[upper]` → free subclass hierarchy; relationships with majority observed
  endpoints) + mined axioms (optional, via ia4.8 `axioms`). `induction_report()`
  gives drift/anchoring diagnostics for the A/B.

Maps to the Keet reasoning triad: cold-start indexing = **abduction** (hypothesize
concrete types under the upper frame) → **induction** (mine the schema/axioms from the
stabilized graph) → **deduction** (materialize entailments). One extraction pass +
a growing soft frame; no forced re-extraction.

## Consequences

- The abstract interface (upper) is what we design against; the concrete schema
  emerges and is versioned/promoted via the lifecycle (ADR-0175/0176). Existing data
  is validated/enriched, not necessarily re-extracted.
- Core landed + tested (upper ontology shape, upper-anchored induction, drift-cluster
  report: Company/Corporation/Regulator → one Organization group). `axioms` mining is
  optional so the module stands alone before ia4.8 merges.
- **WIP:** the live bootstrap EXTRACTION mode (seed `render_upper_frame()` + require an
  `upper` anchor per entity + running-vocabulary feedback) and the cold-start A/B
  (drift / axiom-support / recall vs pure-open) on an instance-diverse corpus. Recall
  hypothesis (small abstract frame does NOT hurt recall like a rich ontology) is the
  key measurement. Design: wiki/cold-start-schema-bootstrap-design.md.
