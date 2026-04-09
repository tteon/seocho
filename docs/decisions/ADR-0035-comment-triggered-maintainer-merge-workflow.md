# ADR-0035: Comment-Triggered Maintainer Merge Workflow

Date: 2026-04-09
Status: Accepted

## Context

SEOCHO's Codex automation opens draft PRs, but maintainers still need a simple
way to land reviewed pull requests without manually pressing the GitHub merge
button every time.

At the same time, comment-triggered automation must remain narrow and explicit:

- merge authority should stay with maintainers
- the trigger should be simple and auditable
- the workflow should not bypass normal branch protection or required checks

## Decision

SEOCHO will add a comment-triggered merge workflow with these rules:

1. trigger on PR comments containing exactly `/go`
2. require the commenter to have repository permission level `write`,
   `maintain`, or `admin`
3. require the target PR to be open and not draft
4. perform a squash merge through GitHub Actions
5. keep branch protection and required checks as the final enforcement layer

## Consequences

Positive:

- reviewed automation PRs and routine maintainer PRs can be landed quickly
- merge intent is visible in PR history as an explicit maintainer comment
- authorization remains narrow and auditable

Tradeoffs:

- maintainers must remember the exact `/go` command
- merge method is standardized to squash, which may not fit every PR shape
- the workflow adds another write-capable automation surface that needs
  repository secret management and monitoring

## Implementation Notes

- workflow lives in `.github/workflows/pr-comment-merge.yml`
- workflow uses the existing GitHub App credential path
- docs are updated in `README.md`, `docs/README.md`, and `docs/WORKFLOW.md`
