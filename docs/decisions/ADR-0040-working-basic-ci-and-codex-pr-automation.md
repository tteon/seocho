# ADR-0040: Working Basic CI And Codex PR Automation

Date: 2026-04-11
Status: Accepted

## Context

SEOCHO had previously removed repository GitHub Actions because the existing
workflow set was no longer trustworthy. That cleanup made the repository
honest, but it also left the project without a real CI/CD path.

The immediate need is not broad automation. The immediate need is a small,
actually working pipeline that:

- validates the repository with commands maintainers already trust locally
- allows Codex to open bounded maintenance/refactor PRs on a schedule
- lets maintainers land a reviewed PR with an explicit `/go` command

## Decision

SEOCHO will restore automation in three layers:

1. Basic CI
   - add `.github/workflows/ci-basic.yml`
   - make `scripts/ci/run_basic_ci.sh` the canonical local and CI command
2. Scheduled Codex draft PRs
   - add `.github/workflows/daily-codex-maintenance.yml`
   - add `.github/workflows/periodic-codex-review.yml`
   - both workflows use `openai/codex-action`
   - both workflows must pass `bash scripts/ci/run_basic_ci.sh` before opening
     or updating a PR
   - both workflows must validate that the PR body includes `Feature`, `Why`,
     `Design`, `Expected Effect`, `Impact Results`, `Validation`, and `Risks`
3. Maintainer-triggered merge
   - add `.github/workflows/pr-comment-merge.yml`
   - only exact `/go` comments from `write`/`maintain`/`admin` users may merge
   - require PR merge state `CLEAN`
   - use squash merge with branch deletion

The repository will not reintroduce broader automation yet. Package publish and
heavier integration workflows remain out of scope until the smaller path proves
stable.

## Consequences

Positive:

- repo automation maps directly to trusted local commands
- scheduled Codex changes stay bounded and review-first
- maintainers regain a simple merge command without restoring broken legacy
  automation

Tradeoffs:

- the CI surface remains intentionally narrow and does not cover all runtime
  paths
- Codex PR automation depends on GitHub App and OpenAI secrets being present
- merge automation still depends on repository branch protection for the
  strongest guarantees

## Implementation Notes

- CI command: `scripts/ci/run_basic_ci.sh`
- PR helper: `scripts/ci/create_or_update_bot_pr.sh`
- workflows:
  - `.github/workflows/ci-basic.yml`
  - `.github/workflows/daily-codex-maintenance.yml`
  - `.github/workflows/periodic-codex-review.yml`
  - `.github/workflows/pr-comment-merge.yml`
