# ADR-0212: Palantir-Ontology-style layered security — dataset → row → cell → sub-cell

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.15 (provenance + fine-grained security)
- Related: ADR-0211 (Postgres ground truth + provenance), ADR-0164 (workspace),
  risk/preflight OntologyDisclosurePolicy (cell), agent/identity AgentPrincipal

## Context

hadry's security model, from the Palantir Ontology: PostgreSQL is the ground truth where
security is authored **semantically** (domain-driven, on the ontology), layered at four
granularities, and the graph is a governed projection carrying the classification forward.
ADR-0211 landed dataset (workspace) + row (RLS) + provenance; this ADR makes all four levels
explicit and adds the two the store did not model directly — with the distinctive **sub-cell**
mechanism the field-level `filter_record` cannot express.

## Decision

`seocho.security_levels` implements the four levels over the shared
`public < internal < restricted < secret` lattice (default-DENY):

- **Level 0 — dataset-backed** (`dataset_visible`): the workspace (`_workspace_id`, ADR-0164).
- **Row-wise (OSP / Restricted View)** (`row_visible`): a whole record visible per clearance; a
  denied row is DROPPED (existence hidden), not returned-but-masked.
- **Cell-level (row × column)**: a property above clearance is masked — reuses
  `risk/preflight.OntologyDisclosurePolicy.filter_record` (NOT re-implemented, review #4).
- **Sub-cell (derived property)** (`filter_array_elements`): the most granular — keeps only the
  ARRAY ELEMENTS within clearance (e.g. one `secret` note inside a list of notes is locked away
  from a lower-clearance principal, the rest returned). The derived-property mechanism the
  field-level filter cannot do.

`SecurityPolicy.apply(record, clearance)` composes all four, returning the visible record (or
`None` when the row is OSP-denied) plus the redaction list for the audit trail. Policies are
expressed as sensitivities on the ontology object type (row / per-property / per-array-element),
so security is domain-driven, not scattered in app code.

## Consequences

- The four Palantir levels are now first-class + composable, on the Postgres ground truth and
  carried into the graph projection (a fact/property/element denied in the ground truth is not
  projected to a principal who cannot see it — the graph is a governed projection, not a bypass).
- Sub-cell array-element protection is the genuinely new capability (field-level `filter_record`
  redacts whole fields); it is deterministic + unit-tested (patient-notes example: general staff
  sees public/internal notes, compliance sees all).
- 6 tests (lattice default-DENY, dataset, row-drop, sub-cell derived filter, four-level
  composition, row-drop→None); `ruff` clean. Reuses the existing lattice + filter_record +
  AgentPrincipal — no duplication. Live enforcement across principals rides ADR-0211's RLS +
  the projection (pg container + psycopg), still gated on that infra.
