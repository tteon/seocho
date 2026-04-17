# ADR-0079: Basic CI Module Ownership Contract

## Status

Accepted

## Context

SEOCHO now has contributor-facing ownership guidance in
`docs/MODULE_OWNERSHIP_MAP.md`, but the most fragile seams are still enforced
mainly through convention. That leaves two practical risks:

- extraction-layer compatibility modules can silently grow new business logic
- focused ownership regression tests can drift out of the default CI surface

The repository already has a runtime-shell contract gate. Indexing and shim
ownership needs the same treatment.

## Decision

`Basic CI` will enforce a module ownership contract for indexing and extraction
compatibility shims.

Rules:

1. Canonical indexing behavior lives under `seocho/index/*`.
2. Canonical rule logic lives under `seocho.rules`.
3. `extraction/rule_constraints.py` and `extraction/vector_store.py` remain
   shim/adapter surfaces.
4. `Basic CI` must run focused tests that verify those shims still delegate to
   canonical owners.
5. `Basic CI` must run a repo-local ownership contract script in addition to
   the runtime-shell contract script.

## Consequences

Positive:

- contributor docs now match an enforceable CI contract
- stale shim tests are pulled back into the required validation surface
- extraction-layer drift is caught earlier

Negative:

- `Basic CI` grows modestly as more focused tests are included
- the contract is string-pattern based in part, so future refactors must update
  the guardrail script intentionally

## Implementation Notes

- script: `scripts/ci/check-module-ownership-contract.sh`
- workflow reference: `docs/WORKFLOW.md`
- contributor ownership map: `docs/MODULE_OWNERSHIP_MAP.md`
