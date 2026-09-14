# ADR-0230: User-data experiment evidence on the existing E2E runner

Date: 2026-09-13
Status: Accepted

## Context

Users need to diagnose their own document-to-answer runs and determine what a
SEOCHO change affected. The locked environment alone cannot establish comparable
inputs or preserve failures. Existing run/sweep, preflight and semantic scorecards
already own the execution and metric contracts.

## Decision

Extend those paths with versioned outcomes, atomic file-backed checkpoints,
content fingerprints and offline matched-report comparison. Separate outcome,
persistence, fingerprint and comparison responsibilities; preserve e2e imports
and existing report fields. Add no public storage plugin or parallel runner.
Partial indexing and blank answers return exit 1; build/stage failures and
interruptions preserve diagnostics. No usable indexing input means query is
skipped. Failed file indexing is not entered into the success tracker.

The new `seocho runs compare` command shows descriptive metric deltas only when
input and question identities agree and other changes are declared. Unknown
observations remain unavailable. It does not choose a winner or infer causal
quality from successful execution. Require a hypothesis for declared changes.

## Consequences

Report schema v2 is additive, but scripts depending on partial failures exiting
0 must adjust. New output directories prevent evidence replacement. Fingerprints
add local read I/O and identify input content without copying the corpus. A
mutable graph, remote service revision, cache state and hardware still require
separate evidence. Content-bearing reports stay local. Strict typing/lint starts
at the new internal boundaries while existing SDK behavior remains compatible.

## Validation

Failure injection covers partial/empty ingestion, build/index interruption,
resource cleanup, output collisions and retry tracking. Comparison tests cover
changed input bytes, model declarations, missing IDs/metrics, legacy reports and
the offline CLI. Basic CI and docs contracts validate the shipped workflow.
No new live-model quality or performance result is claimed by this ADR.
