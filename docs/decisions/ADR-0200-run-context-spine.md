# ADR-0200: Per-request run-context spine (structured multi-agent runtime, step 1)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime), follows the structure investigation
- Related: ADR-0188/0190/0194 (RCU B1/B2/B3), ADR-0164/0171 (tenant isolation)

## Context

A grounded investigation confirmed what the design review's #1 Major independently
flagged: the local `Seocho.ask` path is a monolithic, agent-free function, and the
runtime has **no structured per-request context object** delivered to its stages.
`OntologyRunContext` — a fully-defined "typed middleware envelope … shared by SDK,
runtime, agents, and tools" — was referenced **nowhere** in the runtime. Each stage
re-derived ontology/schema/workspace ad hoc, and there was no notion of "this request
reads ONE frozen ontology version," which multi-tenancy and the e2e's mutation-under-read
probe both require. This is step 1 of structuring the multi-agent runtime: give it a
spine.

## Decision

Build the per-request `OntologyRunContext` **once** in `ask`, workspace-scoped, and expose
it via `_LocalEngine.last_run_context()`. When an RCU pin registry (+ active pointer) is
configured on the engine, the request **pins one frozen ontology version** for its whole
duration via the new `pinned_run_context(...)` context manager (RCU B2 read side, per
`(workspace_id, package_id)`); the pin is released on exit, and the B3 gate cannot reclaim
a version while a request pins it. The drift outcome the pipeline computes is folded back
into the exposed context.

- `OntologyRunContext.with_pinned_version(version, epoch, fingerprint)` + `pinned_epoch`
  record/expose the pinned version immutably (frozen dataclass → a copy).
- `pinned_run_context` reads the active pointer, stamps the pinned version, and holds the
  pin for the request. With no active version it yields the context unchanged.
- All new engine wiring defaults to **off** (`_ontology_pin_registry`/`_active_ontology_pointer`
  = None): with nothing configured, behaviour is byte-for-byte the previous deterministic
  path; the context is simply built and exposed. Pinning activates only when wired.

## Consequences

- The runtime now has ONE typed, workspace-scoped, per-request context object — the spine
  the structured query orchestrator (step 2) will deliver to the query specialist and
  synthesizer instead of each re-deriving from the raw `Ontology`. This closes the review's
  "per-request ontology delivery doesn't exist" gap at the foundation.
- Multi-tenancy is first-class in the spine (`workspace_id` carried explicitly; pins are
  per `(workspace, package)` and isolated across tenants — tested).
- The mutation-under-read guarantee is real and tested: a publish that swaps the active
  version mid-request does not change what the already-pinned request reads. This is what
  makes the e2e OS arm's mutation probe meaningful.
- No behaviour change to the default path; 6 new spine tests + existing run-context /
  drift / ontology-context suites green.

Next (step 2): a structured query orchestrator that composes the schema-grounded query
specialist (`build_graph_agent` + guardrail-as-tool-input-guardrail) and the synthesizer
on the local runtime, consuming this context.
