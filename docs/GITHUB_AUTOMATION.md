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

Reusable shell or Python logic belongs in `scripts/`, then workflows can call
it. Product documentation belongs in `README.md`, `docs/`, or `website/`.

## Contributor-Facing Checks

| Workflow | Local equivalent | Purpose |
|---|---|---|
| `.github/workflows/ci-basic.yml` | `bash scripts/ci/run_basic_ci.sh` | Required SDK/runtime quality gate. |
| `.github/workflows/docs-consistency.yml` | `bash scripts/ci/check-doc-contracts.sh` | Checks repo-side docs contracts. |
| `.github/workflows/docs-site-quality.yml` | `cd website && npm run build` plus site checks | Validates the tracked docs site. |

If a PR fails here, prefer fixing the source code, docs, or reusable scripts
that the workflow calls before patching workflow YAML.

## Contributor Intake

GitHub issue templates collect the minimum information maintainers need:

- bug reports: area, reproduction, expected behavior, actual behavior, environment
- feature requests: problem, proposed shape, acceptance criteria, contribution size
- docs/examples: confusing surface, suggested improvement, validation command

The pull request template mirrors the repository PR contract: `Feature`, `Why`,
`Design`, `Validation`, `Risks / Gaps`, and `Docs`. Maintainers should ask for
exact validation commands before reviewing behavior-changing PRs.

## Maintainer Automation

SEOCHO also has narrow maintainer-only automation:

- scheduled Codex maintenance and review workflows open draft PRs only
- comment-based merge accepts only the exact `/go` command from maintainers with
  write-or-higher permission
- docs deployment runs through GitHub Pages when Pages is enabled

Detailed schedules, required secrets, PR body contracts, and merge safeguards
are documented in `docs/WORKFLOW.md`.

## Placement Rules

- Add new GitHub Actions workflows under `.github/workflows/`.
- Add Codex prompt contracts under `.github/codex/prompts/`.
- Add or update issue templates under `.github/ISSUE_TEMPLATE/`.
- Keep the pull request template at `.github/PULL_REQUEST_TEMPLATE.md`.
- Put reusable automation helpers under `scripts/ci/` or another appropriate
  `scripts/` subdirectory.
- Do not store generated output, credentials, local state, or personal tool
  configuration under `.github/`.
- Do not add `.github/README.md`; keep the repository overview owned by the
  root `README.md`.
- Keep root hierarchy changes covered by
  `scripts/ci/check-root-hierarchy-contract.sh`.
