# ADR-0191: is the ontology useful to text2cypher? (ontology-source A/B, seocho-ia4.13)

Date: 2026-08-16 · Status: accepted (measured) · seocho-ia4.13

## Context

hadry's core question, sharpened: is the ontology genuinely meaningful to the
text2cypher-generation agent? Both arms generate Cypher for the SAME 8 finance-
compliance questions with the SAME model (MARA MiniMax-M2.7); the ONLY variable is the
schema the prompt carries. `scripts/agentos/e2e_ontology_source_ab.py`.

- **THIN**: node labels only (an introspected / name-only view).
- **DECLARED**: the ontology-declared schema (`hybrid_planner.schema_for_prompt`:
  labels + relationship directions/roles + cardinality + properties + tenant scope).

Measured deterministically (no live DB — `validate_text2cypher_fallback` is static):
schema-conformance of the generated Cypher (does it invent labels/relationships/
properties, omit tenant scope, or traverse unbounded).

## Result (8 questions, decisive)

| metric | THIN | DECLARED |
|---|---|---|
| conformance rate | **0%** | **100%** |
| hallucination rate (invented identifiers) | **100%** | **0%** |
| avg violations / query | 2.62 | 0.0 |

With labels only, the generator invents relationship names/directions (`enforces` for
`ENFORCED_BY`), gets the workspace property wrong, and omits tenant scope on every
query. The ontology-declared schema eliminates all of it — 100% conformant, 0
hallucinated identifiers, across all 8.

## Findings

- **The ontology is decisively useful to text2cypher** — not marginally. It supplies
  the exact relationship vocabulary + directions + property names + tenant-scope
  contract the generator otherwise fabricates. A Cypher with a hallucinated label/rel
  cannot execute, so conformance is the necessary condition this ablation isolates.
- **Honest scope**: this measures schema-CONFORMANCE, not end-to-end answer
  correctness (that needs a live DB + gold — AIsummit26's `agent_interaction.py`
  computed-gold harness). Conformance is the floor; the answer-quality layer + the
  profile_gate loop (ADR-0189) + bolt-rs execution are the remaining ia4.13 work.
- Composes with intern_grounding (ADR-0187: resolve request entities to canonical ids)
  and the profile_gate (ADR-0189: detect→profile→improve on plan cost) for the full loop.
