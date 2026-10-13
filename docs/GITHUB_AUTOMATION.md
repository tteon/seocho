# GitHub Automation

SEOCHO uses GitHub-hosted automation for CI, docs checks, docs deploy, and
narrow maintainer workflows. Most contributors should start with
`CONTRIBUTING.md`, `AGENTS.md`, and `docs/WORKFLOW.md` before editing this
surface.

This guide intentionally lives under `docs/`, not `.github/README.md`. GitHub
can display `.github/README.md` as the repository overview, which would hide the
product-first root `README.md`.

## What Belongs In `.github/`

| Path | Purpose |
|---|---|
| `.github/workflows/` | GitHub Actions workflows that run CI, docs checks, docs deploy, and maintainer automation. |
| `.github/codex/prompts/` | Prompt contracts used by scheduled Codex maintenance workflows. |
| `.github/ISSUE_TEMPLATE/` | Public bug, feature, and docs/example intake forms. |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR review envelope matching SEOCHO's public PR contract. |
| `.github/labels.json` | Public issue/PR label taxonomy synced by triage automation. |

Reusable shell or Python logic belongs in `scripts/`, then workflows can call
it. Product documentation belongs in `README.md`, `docs/`, or `website/`.

## Contributor-Facing Checks

| Workflow | Local equivalent | Purpose |
|---|---|---|
| `.github/workflows/ci-basic.yml` | `bash scripts/ci/run_basic_ci.sh` | Required SDK/runtime quality gate across Python 3.10, 3.11, and 3.12. |
| `.github/workflows/docs-consistency.yml` | `bash scripts/ci/check-doc-contracts.sh` | Checks repo-side docs contracts. |
| `.github/workflows/docs-site-quality.yml` | `cd website && npm run build` plus site checks | Required docs/site quality gate for the tracked docs site and the live `seocho.blog` mirror contract. |
| `.github/workflows/docs-website-sync-dispatch.yml` | n/a | Dispatches the `tteon/tteon.github.io` mirror sync after docs changes land on `main` when `SEOCHO_BLOG_SYNC_TOKEN` is configured. |
| `.github/workflows/discord-updates.yml` | n/a | Posts curated updates to Discord only when a release is published or when manually dispatched. |
| `.github/workflows/triage-metadata.yml` | `python scripts/ci/triage_metadata.py --event <event.json>` | Syncs labels and applies `area-*`, `kind-*`, and `status-*` labels to new or updated issues/PRs. |

If a PR fails here, prefer fixing the source code, docs, or reusable scripts
that the workflow calls before patching workflow YAML.

`run_basic_ci.sh` compiles tracked Python source files dynamically, runs a
focused Ruff lint gate on the actively maintained CI/run-spec/onboarding
surfaces, then runs the curated SDK/runtime pytest set and repository contract
checks. Experiment evidence modules additionally run strict mypy with skipped
legacy imports, plus Ruff E4/E7/E9/F/B/UP/SIM rules. This incremental gate covers
run outcomes, persistence, fingerprints, comparisons and the saved-run CLI; it
does not assert that the entire SDK is strictly typed. The locked `ci` and `dev`
extras include mypy. New tests that cover the curated surface should be added to that script
in the same PR as the behavior change; legacy/live-service tests stay outside
Basic CI until their service dependencies and skip contracts are clean.

Docs deployment repeats the in-repo docs quality checks before uploading a
Pages artifact, so a docs source change cannot publish solely because the
Astro build succeeds.

`docs-site-quality.yml` intentionally runs for every PR instead of using path
filters. Required GitHub checks must always be created, otherwise unrelated PRs
can be blocked by a required-but-skipped workflow.

The same gate is available as `make agent-check`. Workspace helper contracts
are covered by `tests/seocho/test_coding_workspace.py`; local receipts remain
untracked. See [Agent Workflow](AGENT_WORKFLOW.md).

## Contributor Intake

GitHub issue templates collect the minimum information maintainers need:

- bug reports: area, reproduction, expected behavior, actual behavior, environment
- feature requests: problem, proposed shape, acceptance criteria, contribution size
- docs/examples: confusing surface, suggested improvement, validation command
- release checklists: version, release type, validation gates, release notes,
  and `#seocho-updates` Discord announcement draft

The pull request template mirrors the repository PR contract: problem/resulting
behavior, design/compatibility, validation, optional experiment evidence, and
risks/documentation. Maintainers should ask for
exact validation commands before reviewing behavior-changing PRs.

The triage workflow checks out only the trusted base branch, even for fork PRs.
It never runs contributor-submitted code; it reads issue form fields, PR titles,
and PR changed-file names through the GitHub API before applying labels.

## Maintainer Automation

SEOCHO also has narrow maintainer-only automation:

- scheduled Codex maintenance and review workflows open draft PRs only
- comment-based merge accepts only the exact `/go` command from maintainers with
  write-or-higher permission
- docs deployment runs through GitHub Pages when Pages is enabled
- live `seocho.blog` mirror sync is owned by `tteon/tteon.github.io`; this
  repository only preflights the presentation contract and dispatches sync
  after source docs land
- Discord update notifications require the `DISCORD_WEBHOOK_URL` repository
  secret and post only releases or manually curated project updates, not
  individual commits or every successful check event
- GraphUserGroup / Ghost and Knowledge OS should own higher-context community
  drafts such as weekly trends, product reviews, job-board opportunities,
  newsletter posts, and digest candidates; the SEOCHO repository workflow
  remains the low-noise release/manual announcement path
- Ghost Admin API automation must stay server-side. Store Ghost Admin API keys,
  Discord webhook URLs, and relay secrets only in GitHub Actions secrets or a
  dedicated server-side secret store.
- Ghost CLI is for self-hosted Ghost install, backup, update, log, and health
  operations. Do not use it as the public content automation path.
- release and Discord community operating rules live in
  `docs/RELEASE_AND_COMMUNITY_OPERATIONS.md`

Detailed schedules, required secrets, PR body contracts, and merge safeguards
are documented in `docs/WORKFLOW.md`.

## Placement Rules

- Add new GitHub Actions workflows under `.github/workflows/`.
- Add Codex prompt contracts under `.github/codex/prompts/`.
- Add or update issue templates under `.github/ISSUE_TEMPLATE/`.
- Keep the pull request template at `.github/PULL_REQUEST_TEMPLATE.md`.
- Update `.github/labels.json` when adding public label vocabulary.
- Put reusable automation helpers under `scripts/ci/` or another appropriate
  `scripts/` subdirectory.
- Do not store generated output, credentials, local state, or personal tool
  configuration under `.github/`.
- Do not add `.github/README.md`; keep the repository overview owned by the
  root `README.md`.
- Keep root hierarchy changes covered by
  `scripts/ci/check-root-hierarchy-contract.sh`.

### Locked experiment environment

Basic CI and scheduled Codex workflows install with `uv sync --locked --extra ci`.
The committed `uv.lock` is checked offline before basic CI runs. The Python
version matrix sets `UV_PYTHON` explicitly so `.python-version` remains a local
default. Use [the experiment environment](EXPERIMENT_PLATFORM.md) to reproduce
these checks in an isolated virtualenv.

Experiment evidence CI covers runtime-setting identity, diagnostic redaction,
query interruption checkpoints, degraded indexing, and safe local HTML rendering.
The code-review connector is a separate account-backed service: its usage-limit
comment is not a successful review, regardless of the CI result.

### Python implementation checks

Basic CI runs product-wide Ruff `E9,F` checks over `src/seocho`, `runtime` and
`extraction`, excluding test directories and notebooks from this additional gate.
Public re-exports must be explicit. The gate includes execution-result JSON,
DataHub approval-tag, registered runtime-tool and semantic-helper compatibility
regressions. Existing focused test/lint and documentation gates remain in place
(ADR-0234).

The basic CI gate also covers the local experiment dashboard catalog, real
loopback HTTP read boundaries, CLI dispatch, and comparison parity. Browser
interaction checks are recorded separately in the dashboard ExecPlan.

The opt-in Graph-RAG collaboration gate (`tests/seocho/test_collaboration.py` and `tests/seocho/test_finder_collaboration.py`, and `tests/seocho/test_fixed_context_designs.py`)
uses scripted model responses to exercise Agents SDK budget and evidence
contracts, original-filing parsing, gold isolation, and source verification
without MARA or DozerDB; live compatibility remains a separate gate.

The basic CI gate includes the Phase 2 Recorder-backed YAML harness matrix in
`tests/seocho/test_agent_observability_harness.py`; deterministic checks do not
substitute for live provider compatibility or answer-quality evaluation.

Phase 3 replay/intervention regression coverage is in
`tests/seocho/test_agent_intervention.py`, including all operators, N sampling,
budget sweeps and pre-dispatch conservative cost caps.

Phase 4 signal tests (`tests/seocho/test_agent_signals.py`) validate derived
mathematics and recorded-source provenance. Scripted verdicts and toy vectors
are contract fixtures, not semantic-model or FinDER quality evidence.

Recorder analytics regression tests are included in basic CI. Local mock report
artifacts remain ignored; no scheduled report runs invoke paid providers. Statistical
and Recorder-backed fixtures verify contracts without asserting model quality.

Basic CI also covers Recorder UI workspace isolation, raw-body inspection,
read-only source preservation, same-origin Mock job submission and positive silver
failure-flow projections. Browser acceptance and packaged assets are local checks;
no UI acceptance enables paid-provider scheduled work.

The basic CI Recorder contract slice also runs `tests/seocho/test_pi_agent.py` for optional Pi JSON framing and failure handling; it does not invoke a remote Pi process or paid provider.
Evaluator isolation, arithmetic/quote receipt checks and T/F stratum handling are
covered by `tests/seocho/test_agent_evaluator.py` in basic CI. Mocks assert unknown
quality; they do not establish evaluator accuracy.

The basic CI contract also runs `tests/seocho/test_agent_tools.py` for Recorder
coverage of model-selected tool loops, parallel operand dependencies, specialist
return and shared call-budget exhaustion. These deterministic fixtures establish
contracts; live MARA compatibility is documented separately in experiment reports.

Basic CI additionally runs `tests/seocho/test_quality_lab.py` for controlled variant
lineage, private-label isolation, coordination context delivery, audit coverage,
suite budget exhaustion and provider-versus-judgment failure distinctions.
