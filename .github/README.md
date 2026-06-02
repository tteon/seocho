# Repository Automation

This directory contains GitHub-hosted automation for SEOCHO. Most contributors
do not need to edit it.

Start with the public contributor docs instead:

- `CONTRIBUTING.md` for the normal PR workflow
- `AGENTS.md` for coding-agent guidance
- `docs/WORKFLOW.md` for maintainer workflow details
- `docs/REPOSITORY_LAYOUT.md` for where new code should live

## What Belongs Here

| Path | Purpose |
|---|---|
| `workflows/` | GitHub Actions workflows that run CI, docs checks, docs deploy, and maintainer automation. |
| `codex/prompts/` | Prompt contracts used by scheduled Codex maintenance workflows. |

Reusable shell or Python logic belongs in `scripts/`, then workflows can call
it. Product documentation belongs in `README.md`, `docs/`, or `website/`.

## Contributor-Facing Checks

| Workflow | Local equivalent | Purpose |
|---|---|---|
| `workflows/ci-basic.yml` | `bash scripts/ci/run_basic_ci.sh` | Required SDK/runtime quality gate. |
| `workflows/docs-consistency.yml` | `bash scripts/ci/check-doc-contracts.sh` | Checks repo-side docs contracts. |
| `workflows/docs-site-quality.yml` | `cd website && npm run build` plus site checks | Validates the tracked docs site. |

If a PR fails here, prefer fixing the source code, docs, or reusable scripts
that the workflow calls instead of patching workflow YAML first.

## Maintainer Automation

SEOCHO also has narrow maintainer-only automation:

- scheduled Codex maintenance and review workflows open draft PRs only
- comment-based merge accepts only the exact `/go` command from maintainers with
  write-or-higher permission
- docs deployment runs through GitHub Pages when Pages is enabled

Detailed schedules, required secrets, PR body contracts, and merge safeguards
are documented in `docs/WORKFLOW.md`.

## Placement Rules

- Add new GitHub Actions workflows under `workflows/`.
- Add Codex prompt contracts under `codex/prompts/`.
- Put reusable automation helpers under `scripts/ci/` or another appropriate
  `scripts/` subdirectory.
- Do not store generated output, credentials, local state, or personal tool
  configuration under `.github/`.
- Keep root hierarchy changes covered by
  `scripts/ci/check-root-hierarchy-contract.sh`.
