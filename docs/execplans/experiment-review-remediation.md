# Repair experiment evidence and remove retired tracing integration

This living plan follows docs/maintainers/EXECPLAN_SPEC.md.

## Purpose / Big Picture

Make saved user-data experiments trustworthy at failure boundaries, remove remaining active Opik integration paths, and provide a local visual inspection surface for reports and module responsibilities.

## Progress

- [x] Reproduced four review findings on main ab742d7 and read prior experiment memory.
- [x] Created isolated codex/review-remediation checkout and claimed beads seocho-o72s.1–.4.
- [x] Correct receipts, redaction, per-question checkpoints and degraded extraction aggregation.
- [x] Remove active third-party Opik paths and normalize current guidance.
- [x] Add local visual report inspection and architectural decision.
- [x] Run focused regressions, basic CI, documentation/site validation.
- [ ] Pass required remote checks and record the landing PR.

## Surprises & Discoveries

Core tracing removed Opik in ADR-0172, but optional imports, setup flags, teaching aliases and notebook calls remained. Teaching notebooks expected a dict from an alias now returning Path. Existing 154 focused tests passed while four additional boundary assertions failed.

## Decision Log

2026-09-13: preserve historical ADRs, archived experiment provenance and local result files. Remove active integration, installation options and current endorsements. Use JSONL/OTLP; no replacement hosted vendor. Fingerprint an explicit runtime-setting contract and version it; old receipts cannot certify new conditions. Render report HTML locally without remote scripts, backend calls or uploads.

## Outcomes & Retrospective

Implementation and local validation completed. Evidence v2 rejects undeclared runtime changes and v1 comparisons; both query paths retain completed answers after interruption. Nested file diagnostics redact known credentials. Degraded extraction is not cached as success. Active Opik setup/imports and current endorsements are removed. The runs CLI exports a local HTML comparison and architectural module map. No live quality/performance result or external Codex review completion is claimed.

## Context and Orientation

src/seocho/e2e.py runs configured indexing and questions. run_evidence.py hashes execution inputs; run_comparison.py compares saved artifacts. run_reporting.py writes JSON/Markdown checkpoints. FileIndexer aggregates extraction records. A checkpoint is the most recent persisted state. A degraded extraction is a heuristic fallback after the requested extraction failed.

## Plan of Work

First fix and test the four existing receipts defects. Then remove remaining Opik execution/setup paths and update teaching source cells plus current contracts. Finally extend the existing runs CLI with a local HTML inspection command and reuse its comparison logic; keep evaluation/server.py chat runtime independent.

## Concrete Steps

From this checkout run uv sync --locked --extra dev; uv run pytest tests/seocho/test_run_recovery.py tests/seocho/test_run_evidence.py tests/seocho/test_file_indexer.py -q; bash scripts/ci/run_basic_ci.sh; bash scripts/ci/check-doc-contracts.sh. Build both site contracts through required GitHub checks before merge.

Local final validation:

- `bash scripts/ci/run_basic_ci.sh`: 1,184 passed, 5 skipped; strict typing of 7 evidence/CLI modules, Python lint, ownership/import/root/environment/agent/identity contracts passed.
- `bash scripts/ci/check-doc-contracts.sh`: passed.
- `cd website && npm run check:docs && npm run build && bash scripts/check-built-links.sh`: passed; 44 pages built. Generated mirrors were regenerated, not edited.
- `pytest tests/seocho/test_run_recovery.py tests/seocho/test_run_visualization.py tests/seocho/test_sweep.py -q`: 24 passed.
- Changed notebook audit: 121 Python cells parsed, 7 tracing setup cells executed, local JSONL span verified; live provider/database cells skipped.
- Headless Chrome synthetic fixture: CSP permits the fixed filter script; text and state filters work; 8 module cards present; 390px viewport has no horizontal overflow. Desktop/mobile screenshots retained locally.
- `bash -n scripts/setup/init-env.sh` and tracked active-source dependency/import scan: passed.

The website dependency install reported existing npm audit advisories; dependency upgrades are outside this tracing/evidence change. Browser fixtures are UI contract evidence only.

## Validation and Acceptance

Undeclared runtime changes reject aggregate comparison. Synthetic endpoint secrets never enter saved failure diagnostics or CLI errors. Completed Q1 survives interruption of Q2 in direct and Agents SDK loops. Degraded files retain reasons and are not cached as successes. Current setup, runnable examples and dependencies contain no Opik integration. HTML shows missing evidence as unavailable and architectural modules separately from observed execution.

## Idempotence and Recovery

Work in an isolated worktree; never clean or reset original research. Reports and HTML outputs refuse overwriting existing targets. Historical receipts remain immutable and comparable only under their supported contract. New changes can be reverted by their scoped commits.

## Artifacts and Notes

Review and failing reproductions remain in the ignored common .seocho/pr-review/20260913-delivery directory. Beads seocho-o72s.1–.4 are fixes, .6 removes Opik, .7 tracks visualization. Account quota recovery .5 remains independent and requires owner account access.

## Interfaces and Dependencies

Reuse RunSpec, ReportStore, query_state and compare_runs. Add pure diagnostic redaction and run visualization modules with typed interfaces. Standalone HTML uses no JavaScript framework or network assets. Preserve existing SDK contracts and CLI subcommand dispatch.

## SEOCHO Evidence Contract

Preserve intent, selected relation triples, required/missing slots, support and provenance in completed question records. Fingerprints identify conditions rather than proving causal quality. Source/module maps are descriptions, never fabricated spans. Existing backend policy and workspace propagation remain in force.

## SEOCHO Review Panel

Semantic lens: no synthetic quality or invented module timings. Engineering lens: one owner each for execution, redaction, persistence, comparison and presentation; regressions reproduce boundary behavior. Systems lens: per-question durable writes add local filesystem work; no extra provider calls or hosted service. Falsify with interruption, unsupported receipt, malicious HTML content and missing telemetry tests.

## Cost, Latency, and Provider Policy

No new paid calls or live benchmarks. Existing local contract tests validate failures, not throughput or backend compatibility. OTLP exporters are operator-selected; default report visualization is offline.
