# ADR-0202: Structured query orchestrator + arm×organ config (structured runtime, step 2b)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime)
- Related: ADR-0200 (run-context spine), ADR-0201 (concurrency + pinned resolver),
  ADR-0164/0171 (isolation), ADR-0191 (schema grounding)

## Context

The local `Seocho.ask` path was a monolithic, agent-free function. The orchestrator design
review returned go-with-fixes; the e2e redesign fixed the arm design into an explicit
**arm×organ matrix** (wiki/e2e-redesign-arm-organ-matrix.md), reframed — per hadry, and
matching the AgenticOS CFP's own words — as a **measured agent-OS layer**. This lands the
orchestrator that makes the five governed-memory organs independent runtime flags.

## Decision

- **`ArmConfig`** (query/arm_config.py): the five organs — canonical address space (intern),
  schema grounding (pinned vs introspected), version isolation (RCU pin), access isolation
  (workspace enforcement), query safety (guardrail) — each an independent flag. `bare()` =
  all off (a *real* bare RAG, not a strawman), `governed()` = all on, `without(organ)` = the
  five leave-one-outs. `ablation_arms()` is the principled, power-aware set (BARE + GOVERNED
  + 5 LOO), NOT the 2^5 grid.

- **`StructuredQueryOrchestrator`** (query/structured_orchestrator.py): a **plain
  deterministic function** over those flags (NOT an LLM manager wrapping a specialist — the
  review's "inflated ceremony"). One honest flow: resolve schema → generate Cypher (retrieve
  step) → guardrail → execute → synthesize. Applying the review:
  - **B1/schema:** pinned frozen-snapshot schema via `PinnedSchemaResolver` (ADR-0201) vs a
    REAL DB-introspected labels/rel-types baseline.
  - **B2/workspace:** governed execute force-pins the workspace + `enforce_workspace_filter`
    when the organ is ON; a plain read when OFF.
  - **B3/guardrail:** validates against the policy compiled from the SAME pinned snapshot as
    the prompt schema — enforcer and prompt cannot disagree.
  - **B5/synthesis:** the Cypher generator is retrieve-only; the synthesizer alone writes the
    prose, so the answer-quality metric attaches to one controlled surface.
  The LLM text2cypher and graph execution are injected SEAMS, so organ semantics are
  testable without live infra.

## Consequences

- The arm×organ matrix is executable: BARE / GOVERNED / the five leave-one-outs are points in
  one flag space the harness flips — the per-organ ablation the e2e redesign requires.
- 6 orchestrator tests + 5 arm-config assertions prove each organ changes execution
  deterministically (schema source, forced workspace + `enforce_workspace_filter`, guardrail
  rejects-and-does-not-execute unsafe Cypher, synthesizer-owns-prose, per-tenant workspace on
  every query). The monolithic composition is now supplanted by a structured, organ-flagged,
  tenant-safe orchestrator.

## Remaining (step 2c)

Wire the orchestrator into `Seocho.ask` behind an orthogonal `engine=deterministic|structured`
axis (NOT overloading `query_mode`), with the real `cypher_generator` (a schema-grounded
`build_graph_agent`) and the real `QueryAnswerSynthesizer`, plus a per-request `GuardrailLedger`
— validated end-to-end with live MARA/DozerDB as part of the e2e. bolt-rs (the I/O-plane organ,
AIsummit26 rust-harness) is on the roadmap as the sixth organ.
