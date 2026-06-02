# GitHub Automation

This directory owns repository-hosted GitHub workflows and Codex automation
prompts. Keep product documentation in `docs/` or `website/`; keep reusable
local automation in `scripts/`.

## Layout

| Path | Role |
|---|---|
| `workflows/ci-basic.yml` | Required Python/runtime/SDK quality gate. Runs `bash scripts/ci/run_basic_ci.sh`. |
| `workflows/docs-consistency.yml` | Verifies the repo-side docs contract with `bash scripts/ci/check-doc-contracts.sh`. |
| `workflows/docs-site-quality.yml` | Checks and builds the tracked Astro/Starlight site under `website/`. |
| `workflows/docs-site-deploy.yml` | Deploys `seocho.blog` through GitHub Pages when Pages is enabled. |
| `workflows/daily-codex-maintenance.yml` | Runs one small scheduled maintenance pass and opens or updates a draft PR. |
| `workflows/periodic-codex-review.yml` | Runs one broader but still bounded repository review pass and opens or updates a draft PR. |
| `workflows/pr-comment-merge.yml` | Lets maintainers squash-merge a clean, non-draft PR with the exact `/go` comment. |
| `codex/prompts/daily-maintenance-pr.md` | Prompt contract for the daily Codex maintenance lane. |
| `codex/prompts/periodic-review-pr.md` | Prompt contract for the periodic Codex review lane. |

## Automation Lanes

| Lane | Branch | PR title | Validation |
|---|---|---|---|
| Daily maintenance | `codex/daily-maintenance` | `chore: codex daily maintenance` | `bash scripts/ci/run_basic_ci.sh` |
| Periodic review | `codex/periodic-review` | `refactor: codex periodic review` | `bash scripts/ci/run_basic_ci.sh` |

Both Codex lanes must produce draft PRs only, choose one cohesive change, and
include the PR body sections enforced by `scripts/ci/validate_pr_body.sh`:
`Feature`, `Why`, `Design`, `Expected Effect`, `Impact Results`,
`Validation`, and `Risks`.

Scheduled Codex workflows skip cleanly when any required secret is missing:
`OPENAI_API_KEY`, `SEOCHO_GITHUB_APP_ID`, or
`SEOCHO_GITHUB_APP_PRIVATE_KEY`.

## Merge Rule

The comment-based merge workflow is intentionally narrow:

- trigger comment must be exactly `/go`
- commenter must have `write`, `maintain`, or `admin` permission
- PR must be open, non-draft, and have merge state `CLEAN`
- merge method is squash, with branch deletion enabled

## Placement Rules

- Add new CI or repository automation workflows under `workflows/`.
- Add Codex prompt contracts under `codex/prompts/`.
- Put reusable shell/Python helpers in `scripts/ci/` or another appropriate
  `scripts/` subdirectory, then call them from workflows.
- Do not store generated docs-site output, credentials, local state, or
  personal tool configuration under `.github/`.
- Keep public root hierarchy changes covered by
  `scripts/ci/check-root-hierarchy-contract.sh`.
