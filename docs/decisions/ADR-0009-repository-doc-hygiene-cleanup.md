# ADR-0009: Repository Doc Hygiene Cleanup

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

The repository still contained obsolete archived docs and local scratch paths
that created noise during day-to-day collaboration.

## Decision

1. Remove outdated archived docs from git tracking:
   - `docs/archive/ARCHITECTURE_REVIEW.md`
   - `docs/archive/LANCEDB_MIGRATION.md`
2. Update docs index to reflect archive status.
3. Add local scratch directories to `.gitignore`:
   - `daemon/`
   - `gnnllm/`
   - `seocho/`

## Consequences

Positive:

- cleaner GitHub surface for contributors
- reduced accidental inclusion of local workspace artifacts

Trade-offs:

- removed files are only available via git history if needed later
