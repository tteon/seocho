# ADR-0205: engine="structured" axis — wire the orchestrator into Seocho.ask (Step 2c/1)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime)
- Related: ADR-0200/0201 (run-context + resolver), ADR-0202 (orchestrator),
  ADR-0203/0204 (read-side resolver + convergence), the multi-agent-flow review (D1/D5)

## Context

The organ-flagged `StructuredQueryOrchestrator` (ADR-0202) existed as a composable unit but
was not reachable from `Seocho.ask` — the default path was still the monolithic,
agent-free `_run_query_pipeline`. The orchestrator review's major asked for an **orthogonal
engine axis** (not overloading `query_mode`, which carries reasoning semantics), governed
reads (D1), honest abstain (D5), and a **per-request** GuardrailLedger.

## Decision

Add an `engine` parameter — `"deterministic"` (default, unchanged) or `"structured"` — to
`Seocho.ask` / `ask_response` and `_LocalEngine.ask`, orthogonal to `query_mode`.

- `engine="structured"` routes, inside the existing per-request pin + ContextVar wrapper, to
  `_run_structured_pipeline`, which builds a `StructuredQueryOrchestrator` over the request's
  run context and drives resolve-schema → retrieve (cypher generator seam) → guardrail →
  governed execute → synthesize (synthesizer seam owns the prose, B5).
- **Governed reads (D1/B2):** the workspace organ ON makes execution force-pin the workspace
  and set `enforce_workspace_filter`; nothing else touches the store.
- **Honest abstain (D5):** `answer_source` distinguishes `structured` (answered),
  `structured_no_evidence` (empty rows), and `structured_guardrail_rejected` (unsafe Cypher
  refused) — a rejection is never reported as "no supporting evidence."
- **Per-request GuardrailLedger:** built fresh per call, so the before/after governance
  signal (rejection rate) is never poisoned across tenants/requests (the review's major).
- The two seams (LLM text2cypher, `QueryAnswerSynthesizer`) default to the real
  implementations but are **injectable** (`_structured_cypher_generator`,
  `_structured_synthesizer`), so organ semantics are unit-tested without live LLM/DB; a
  snapshot-store-backed `_pinned_schema_resolver` enables the pinned-schema organ.

## Consequences

- `Seocho.ask(..., engine="structured")` runs the governed, organ-flagged path; the default
  `"deterministic"` path is byte-for-byte unchanged (fully additive).
- **Refcount leak-safety (hadry's OS-resource concern):** the structured pipeline runs inside
  the `pinned_run_context` + ContextVar wrapper, whose `finally` releases the pin even on an
  exception — tested (a raising body releases the version pin; `min_pinned_epoch` returns to
  None).
- 4 new wiring tests (governed execute + metadata, honest abstain on guardrail reject, bare
  arm skips the filter, refcount leak-safety); client/run-context/orchestrator suites green
  (24 together). `ruff` clean.

## Remaining (Step 2c/2)
The default seams are the real LLM text2cypher + synthesizer; end-to-end validation on live
MARA/DozerDB is the e2e itself. D3 single-federated-graph indexing (with `(id, _workspace_id)`
MERGE per review #6) and D4 scheduler per-tenant fairness remain. bolt-rs is the I/O-plane organ
on the roadmap.
