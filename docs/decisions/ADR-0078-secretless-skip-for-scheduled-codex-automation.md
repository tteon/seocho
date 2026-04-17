# ADR-0078: Secretless Skip For Scheduled Codex Automation

## Status

Accepted

## Context

SEOCHO keeps `Basic CI` as the required repository quality gate. Scheduled
Codex automation is optional operational tooling on top of that baseline.

The current daily and periodic Codex workflows fail immediately when
`OPENAI_API_KEY`, `SEOCHO_GITHUB_APP_ID`, or
`SEOCHO_GITHUB_APP_PRIVATE_KEY` are missing. In repositories or forks where
those secrets are intentionally absent, the result is a red GitHub Actions
surface even though product CI is healthy and no maintainer action is required.

## Decision

Scheduled Codex automation will treat missing automation secrets as an explicit
skip, not a failing CI condition.

Rules:

1. `Basic CI` remains the required check surface for repository health.
2. Daily and periodic Codex workflows first check for automation secrets.
3. When one or more required secrets are missing, the workflow emits a notice
   and exits successfully without running Codex or opening a PR.
4. When all required secrets are present, the existing Codex automation flow
   runs unchanged.

## Consequences

Positive:

- scheduled automation no longer creates false-negative CI failures
- forks and secretless environments keep a clean Actions surface
- maintainers can distinguish product regressions from optional automation setup

Negative:

- missing secret configuration can be overlooked unless maintainers watch the
  skip notice
- scheduled automation health is no longer represented by a red failed run

## Implementation Notes

- workflows:
  - `.github/workflows/daily-codex-maintenance.yml`
  - `.github/workflows/periodic-codex-review.yml`
- workflow reference: `docs/WORKFLOW.md`
- repository entry note: `README.md`
