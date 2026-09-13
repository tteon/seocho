# User-data E2E diagnostics and experiment comparison

## Purpose / Big Picture


Users should run their own documents through the existing `seocho run` command,
find the stage that failed, and compare saved baseline/candidate reports without
another model call. This is an experiment platform improvement, not a new hosted
product or a fifth backend plugin surface. Work item: seocho-qf8m.

## Progress


- [x] Read main implementation, local experiment memory, previous setup receipts,
  scorecards and tracker; isolate changes from the dirty research checkout.
- [x] Make run outcomes, artifacts, preflight and cleanup reliable.
- [x] Add versioned input/source evidence and offline matched comparison.
- [x] Add contract tests, focused Python type/lint gates and user documentation.
- [ ] Validate, review, merge and verify main.

## Surprises & Discoveries


RunReport.ok accepts partial indexing failure and empty answers. A stage-level
exception can prevent report creation. Output directories have second-resolution
names and permit overwriting reports. Preflight queries the default database
rather than the run target and recommends an unsupported embedded fallback.
The README describes embedded run behavior although RunSpec requires Bolt.
FileIndexer also entered failed writes into its success tracker, hiding retry
work as unchanged; now only successful writes are tracked. A strict new-module
type gate required mypy in ci/dev; the lock added mypy dependencies without
changing any existing dependency version.

## Decision Log


DEV-DECISION: extend run/sweep and existing semantic scorecards; do not create a
parallel runner, experiment database, or generic MetadataStore.
DEV-CONSTRAINT: preserve public SDK/query semantics, workspace propagation,
DozerDB baseline, original artifacts, and the uncommitted research checkout.
DEV-API-CONTRACT: reports gain versioned status, diagnostics and reproducibility
receipts. Existing report keys and e2e imports stay compatible. Partial failure
returns exit 1. Offline comparison refuses aggregate deltas for unmatched inputs.
DEV-ACCEPTANCE: injected failures produce inspectable reports, no output is
silently overwritten, identical inputs can be matched across source revisions,
and changed questions/corpora cannot produce a claimed matched improvement.

## Outcomes & Retrospective


Implemented versioned report checkpoints, diagnostics, input/source evidence,
offline matched comparison, no-track CLI and failed-index retry handling. Focused
validation: 125 passed. Strict mypy: 5 modules pass. Basic CI before the final
three test additions: 1158 passed, 5 skipped; final CI and remote checks pending.
Docs and ADR contracts passed. Generated a user project and ran offline dry-run
successfully. No new quality or performance measurement has been made.

## Context and Orientation


src/seocho/e2e.py currently owns construction, phase execution, rendering and
sweep orchestration. run_spec.py validates YAML; run_preflight.py checks local
inputs and graph readiness. eval/semantic_scorecard.py already provides outcome
proxies. run_template.py already isolates sweep variants. Keep these contracts.

The closest shipped arm is main 61cbbab2 (#674): locked Python/CI environment,
1133 passing basic tests, no public specialist runner. Local prior records under
.seocho/platform/20260913 and .seocho/benchmarks/seocho-context-20260912 report
actual readiness and 4 episodes/22 calls/158 spans. These measurements belong to
a different source/service snapshot and are not reused as new E2E evidence.
The specialist diagnostics are implemented in local research code; this work
promotes none of those unrelated changes. Generic user-data comparison is new.

## Plan of Work


First separate report status/rendering/persistence from E2E orchestration, preserve
failed stage evidence and fix preflight/onboarding contradictions. Then record
content digests and effective run conditions before execution, and add an offline
comparison command using matched question IDs and the existing scorecard. Finally
exercise failures and comparison boundaries, enforce typing on the new seams,
and document an exact own-data baseline/candidate workflow.

## Concrete Steps


Use a branch from origin/main in an isolated worktree. Run focused tests first,
then bash scripts/ci/run_basic_ci.sh and the documentation/root/ADR contracts.
Use saved local fixture reports for offline CLI acceptance. Do not launch paid
model calls or overwrite an existing graph dataset for this tooling validation.

## Validation and Acceptance


Cover partial/empty indexing, blank answers, fatal build/index/query failures,
resource cleanup, output collision, requested database preflight, duplicate IDs,
changed input contents and model/settings conditions, missing/invalid metrics,
legacy reports, and per-question regression transitions. Source-version changes
must be declared; quality proxies must not be named answer accuracy. A no-service
fixture validates the reporting contract only. Live backend/answer-quality gates
remain explicitly separate if no new service run is performed.

## Idempotence and Recovery


Use unique output directories and refuse existing report targets. Preserve JSON
checkpoints through interruption. Retrying uses a fresh output directory; it does
not imply rollback of graph writes. Keep all original research data and receipts.

## Artifacts and Notes


Private validation logs belong under .seocho/validation in the implementation
worktree. Public contracts and tests reference no local absolute paths or secrets.

## Interfaces and Dependencies


The file-backed report writer is an internal persistence seam, not a public
backend plugin. Typed status/diagnostic contracts and pure comparison helpers
separate evidence interpretation from model/graph calls. Existing semantic
scorecards remain the metric owner. Strict type checking is incremental at these
new boundaries, not a claim that the entire SDK is statically verified.

## SEOCHO Evidence Contract


Preserve existing ontology signals, required answer slots, relation paths,
provenance and insufficiency from query envelopes. A slot is an answer field;
provenance identifies its supporting source. Do not synthesize missing evidence
or treat a returned answer as correct. Compare matched question/reference sets.

## SEOCHO Review Panel


Research lens: matched input digests and explicit changed conditions prevent
accidental dataset substitution; a comparison remains descriptive, not causal
proof. Engineering lens: separate persistence/status/comparison, preserve imports,
and validate observable failure behavior. Systems lens: hash files in chunks,
keep analysis offline and preserve unknown token/cost/service telemetry. Revisit
this design if fingerprinting is material for actual user corpora or artifacts
cannot represent an interrupted run faithfully.

## Cost, Latency, and Provider Policy


Incremental hypothesis: faithful failure receipts and matched comparisons make
user-data regressions diagnosable without repeated model calls. This is a tooling
hypothesis, not a claim that SEOCHO answers improve. No paid calls are needed to
validate it. Hosted and self-hosted results remain separate; latency comparisons
require recorded external conditions before being used as performance evidence.

## Scope follow-up


The user additionally requested more records, an agent-oriented local workspace
cleanup and a substantial GitHub/README redesign. Tasks seocho-89go and
seocho-6gbq track those separately so this execution change stays reviewable.
