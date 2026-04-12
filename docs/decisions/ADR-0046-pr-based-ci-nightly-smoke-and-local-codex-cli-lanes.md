# ADR-0046: PR-Based CI, Nightly Smoke, and Local Codex CLI Lanes

## Status

Accepted

## Context

SEOCHO had two conflicting automation models:

1. deterministic GitHub CI checks for pull requests
2. GitHub-hosted Codex draft-PR generation that required repository-side API
   credentials

The repository now wants:

- deterministic CI in GitHub
- bounded Codex automation driven locally through Codex CLI
- Jules to act as PR-fixer-first on existing PRs
- a scheduled runtime smoke gate that is separate from PR authoring

## Decision

1. `.github/workflows/ci.yml` is the canonical GitHub PR CI workflow.
2. `.github/workflows/ci-basic.yml` remains legacy/manual only.
3. `.github/workflows/nightly-e2e-smoke.yml` is added as the deterministic
   runtime smoke workflow.
4. GitHub-hosted Codex draft-PR workflows are removed.
5. Local Codex CLI lane runners become the bounded PR authoring path:
   - `feature-improvement`
   - `refactor`
   - `e2e-investigation`
6. Repo-local skills and local prompt files define the contract for each lane.
7. Jules remains PR-fixer-first and should only repair failing CI or directly
   related narrow issues on an existing PR.

## Consequences

### Positive

- PR validation stays deterministic and GitHub-native.
- Codex authoring no longer depends on GitHub-side OpenAI API credentials.
- runtime smoke failures now map cleanly to a dedicated `e2e-investigation`
  lane
- automation roles are clearer:
  - CI validates
  - Codex authors bounded PRs
  - Jules repairs CI
  - maintainers merge

### Negative

- local automation now assumes a clean dedicated clone with `codex` and `gh`
  available
- there is no repository-side autonomous Codex PR creation anymore
- local runner misuse can still widen scope if prompts or skills drift

## Supersedes

This ADR supersedes the GitHub-hosted Codex workflow direction from
`ADR-0040-working-basic-ci-and-codex-pr-automation.md` while retaining the PR
body contract and maintainer-triggered `/go` merge model.
