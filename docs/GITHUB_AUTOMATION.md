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
checks. New tests that cover the curated surface should be added to that script
in the same PR as the behavior change; legacy/live-service tests stay outside
Basic CI until their service dependencies and skip contracts are clean.

Docs deployment repeats the in-repo docs quality checks before uploading a
Pages artifact, so a docs source change cannot publish solely because the
Astro build succeeds.

`docs-site-quality.yml` intentionally runs for every PR instead of using path
filters. Required GitHub checks must always be created, otherwise unrelated PRs
can be blocked by a required-but-skipped workflow.

## Contributor Intake

GitHub issue templates collect the minimum information maintainers need:

- bug reports: area, reproduction, expected behavior, actual behavior, environment
- feature requests: problem, proposed shape, acceptance criteria, contribution size
- docs/examples: confusing surface, suggested improvement, validation command
- release checklists: version, release type, validation gates, release notes,
  and `#seocho-updates` Discord announcement draft

The pull request template mirrors the repository PR contract: `Feature`, `Why`,
`Design`, `Validation`, `Risks / Gaps`, and `Docs`. Maintainers should ask for
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
