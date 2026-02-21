# ADR-0018: User Activation Priority and Docs Sync Contract

- Date: 2026-02-21
- Status: Accepted

## Context

SEOCHO now has integration-level runtime validation, but user adoption depends on a reproducible first-run path and clear extension docs.

Recent incidents also showed that architecture priorities were implied across discussions but not explicitly fixed in execution order.

The documentation website (`seocho.blog` via `tteon.github.io`) requires stable source-of-truth docs so updates can be synchronized without ambiguity.

## Decision

Define and enforce the following as active architecture and documentation policy:

1. User activation critical path is a release gate:
   - raw ingest (`/platform/ingest/raw`)
   - fulltext ensure (`/indexes/fulltext/ensure`)
   - semantic/debate chat (`/api/chat/send`)
   - strict smoke verification (`make e2e-smoke`)

2. Architecture priority order is explicit:
   - P0 runtime contract stability (SDK adapter + contract tests)
   - P0 real-database-only agent provisioning and degraded-state handling
   - P1 `id` to `elementId` query durability migration
   - P1 runtime-vs-batch health/process isolation
   - P1 agent readiness state machine for routing and supervision
   - P2 governance automation around `/rules/assess`

3. Docs sync contract for website publishing:
   - keep these documents in lockstep for release notes and blog sync:
     - `docs/README.md`
     - `docs/QUICKSTART.md`
     - `docs/ARCHITECTURE.md`
     - `docs/WORKFLOW.md`

4. Add open-source extension guidance as a first-class document (`docs/OPEN_SOURCE_PLAYBOOK.md`).

## Consequences

Positive:

- clearer implementation order for architecture-significant work
- faster onboarding for users who want to test raw data to chat outcomes
- less documentation drift between repository and website sync target
- better contribution quality for external open-source adopters

Tradeoffs:

- docs maintenance burden increases for each runtime behavior change
- release flow now includes explicit quickstart reproducibility checks

## Implementation Notes

Updated documents:

- `CLAUDE.md`
- `README.md`
- `docs/README.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`
- `docs/OPEN_SOURCE_PLAYBOOK.md`
