# ADR-0208: Ontology-grounded text2cypher for the structured engine (live-validated)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime), seocho-ia4.13 (text2cypher)
- Related: ADR-0191 (ontology-source A/B), ADR-0201 (pinned resolver), ADR-0205 (engine axis),
  ADR-0206 (workspace-scoped MERGE)

## Context

The first live smoke of `engine="structured"` against DozerDB+MARA proved the path runs, but
the governed arm ABSTAINED on a question whose answer existed: the default cypher generator
(the deterministic planner) emitted Cypher with undeclared properties, inlined literals, and
no tenant scope, so the guardrail (rightly) rejected it. That is the exact confound the e2e
review warned about — the governed arm would look "worse" for the wrong reason (a generator
defect), not because governance costs anything.

## Decision

Add `query/grounded_text2cypher.generate_grounded_cypher(llm, question, schema_text, *,
workspace_id, limit) -> (cypher, params)` and make it the structured engine's default cypher
generator. It emits guardrail-conformant Cypher **by construction**, grounded in the pinned
schema (ADR-0201): declared identifiers only, every value a `$param` (never inlined),
`{_workspace_id: $workspace_id}` on **every** node (so the store's `verify_workspace_binding`
passes — every RETURNed node is scoped, not just the anchor), and `LIMIT $limit`. It returns
the value params so the orchestrator can bind them for both the guardrail check and execution
(a value never has to be inlined to be executable).

`StructuredQueryOrchestrator` now accepts `(cypher, params)` from the generator (back-compat:
a bare string still works) and threads the params into the guardrail validation and the
governed execute.

## Consequences

- **Live-validated:** with the grounded generator, the governed arm produces
  `MATCH (i:Incident {_workspace_id:$workspace_id})-[:AFFECTS]->(c:Company
  {_workspace_id:$workspace_id, name:$company_name}) RETURN i LIMIT $limit`, the guardrail
  PASSES (`allowed:1, rejected:0`), the store's `enforce_workspace_filter` passes, and the
  answer is correct (the real incident) — versus the ungoverned deterministic path's run-to-run
  confabulation. The Plane-2 blocker (governed spuriously abstaining) is removed.
- This wires the "ontology grounds the query" thesis (ADR-0191, measured 0→100% conformance)
  into the RUNTIME, not just an ablation: the pinned schema drives a generator that satisfies
  BOTH governance gates (the ontology guardrail AND the store's workspace-binding proof).
- 3 unit tests (prompt contract, param hygiene incl. stripping runtime-bound params, malformed
  output → empty); orchestrator/engine suites green (13). Reproducible live asset:
  `scripts/agentos/e2e_structured_smoke.py`.

## Note
The generator relies on the LLM; a repair loop (feed the guardrail's violation reasons back
for a retry) is a natural follow-up if a model emits non-conformant Cypher on harder schemas.
Today it is conformant-by-prompt and validated on the enterprise smoke.
