# ADR-0201: Concurrency-safe run context + pinned-schema resolver (structured runtime, step 2a)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime)
- Related: ADR-0200 (run-context spine), ADR-0117 (snapshot store), ADR-0188/0190 (RCU)

## Context

A multi-persona design review of the Step 2 structured query orchestrator returned
**go-with-fixes with 7 blockers**. Two are foundational and must land before the
orchestrator; one of them is a defect in the ADR-0200 code already on main:

- **B7 (shipped defect):** the in-flight run context was stored on a shared instance
  attribute (`self._current_run_context`), and `ask`'s `finally` read it back. Under the
  concurrent multi-tenant path the e2e exercises, two in-flight `ask()`s clobber it, so
  tenant A's workspace/pin can attach to tenant B's exposed context.
- **B1 (unanimous):** the run-context pin only *stamps* the pinned `(version, epoch)`; it
  carries no schema/ontology. So "the specialist reads the pinned schema" was not
  expressible — the specialist would fall back to `schema_for_prompt(self.ontology)` on the
  LIVE, mutable ontology, and a mid-request publish would still change what it reads. "The
  OS delivers ONE pinned ontology per request" was asserted, not real.

## Decision

**B7 — ContextVar isolation.** The in-flight context now lives in a module-level
`contextvars.ContextVar` (`_ACTIVE_RUN_CONTEXT`), set per request and read via
`active_run_context()` — isolated per execution context, never a shared attribute. `ask`'s
`finally` folds the drift outcome into the **local** `pinned_context`, never `self.*`.
`_LocalEngine.last_run_context()` remains only as a documented **post-hoc, NOT
concurrency-safe** convenience (reflects whichever request finished last); mid-run consumers
use `active_run_context()`.

**B1 — PinnedSchemaResolver.** New `query/pinned_schema.py`: given `(package_id, version)`
it loads the immutable snapshot (`OntologySnapshotStore`) and compiles BOTH the prompt
schema (`schema_for_prompt`) AND the Cypher-validation policy (`policy_from_ontology`) from
that ONE frozen ontology — so the schema the specialist is shown and the guardrail policy
that enforces it can never disagree (pre-empts B3). `resolve_for(run_context)` reads the
pin off the run-context. The compiled block is pure, tenant-agnostic data, so it is cached
by `(package_id, version, fingerprint)` — and **only** that data is cached, never a
workspace-bound Agent or tool (pre-empts B6's cross-tenant cache leak).

## Consequences

- Concurrent multi-tenant requests no longer share in-flight context state (B7 test: two
  threads each see only their own tenant's run context; the clobber-prone attribute is
  gone).
- "Per-request pinned ontology delivery" is now REAL and testable: a request that pinned
  1.0.0 resolves 1.0.0's frozen schema even after 2.0.0 is published (the frozen-read
  guarantee the mutation probe relies on), and prompt-schema + guardrail-policy derive from
  the same snapshot.
- These are the honest foundation the Step 2b orchestrator builds on. 13 new tests; the
  run-context / ontology-context / drift / snapshot / RCU suites remain green.

## Deferred to step 2b (the remaining review blockers/majors)

B2 (route specialist execution through governed `SeochoOS.execute_query` with force-pinned
workspace + `enforce_workspace_filter`); B3 (guardrail policy from the pinned snapshot per
call — resolver already provides it); B4 (explicit arm×organ on/off matrix + a real
introspected-schema provider for BARE, or drop it); B5 (specialist retrieves-only, one
synthesizer owns prose, populate `evidence_state`); B6 (no fingerprint-keyed Agent cache —
resolver already caches only the pure schema block). Majors: orchestrator = plain
deterministic function (not an LLM manager over one specialist); declare the repair-loop
fork; converge on ONE governed agent builder; add an orthogonal `engine` axis instead of
overloading `query_mode`; per-request `GuardrailLedger`.
