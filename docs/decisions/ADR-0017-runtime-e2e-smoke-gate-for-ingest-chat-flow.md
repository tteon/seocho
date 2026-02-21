# ADR-0017: Runtime E2E Smoke Gate for Ingest-Chat Flow

- Date: 2026-02-21
- Status: Accepted

## Context

SEOCHO runtime behavior depends on multiple coupled modules (ingestion, DB load, semantic routing, chat interface, and debate orchestration).

Unit tests alone are insufficient to catch cross-boundary regressions in this flow.

## Decision

Add a dockerized runtime smoke gate for integration coverage:

- create `scripts/integration/e2e_runtime_smoke.sh` for end-to-end API checks
- include runtime path checks:
  - raw ingest (`/platform/ingest/raw`)
  - fulltext ensure (`/indexes/fulltext/ensure`)
  - semantic chat (`/api/chat/send`, mode `semantic`)
  - debate chat (`/api/chat/send`, mode `debate`)
- add GitHub Actions workflow `.github/workflows/integration-e2e.yml`
- add local entrypoint `make e2e-smoke`

Debate handling policy:

- strict pass requirement when valid `OPENAI_API_KEY` is provided
- non-strict smoke validation when key is absent/dummy (endpoint reachability + JSON response)

## Consequences

Positive:

- catches runtime integration breakages earlier
- creates a repeatable verification baseline for raw-data-to-chat usability
- aligns CI checks with product-critical user flow

Tradeoffs:

- longer CI runtime due dockerized services
- dual-mode debate assertions add some complexity

## Implementation Notes

- workflow file: `.github/workflows/integration-e2e.yml`
- smoke script: `scripts/integration/e2e_runtime_smoke.sh`
- make target: `make e2e-smoke`
