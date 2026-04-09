# ADR-0032: Daily Codex GitHub App Maintenance Workflow

Date: 2026-04-09
Status: Accepted

## Context

SEOCHO has a growing set of recurring maintenance tasks:

- docs consistency updates
- focused regression coverage
- small workflow hardening changes
- low-risk contributor experience fixes

The repository already has project-level guidance in `AGENTS.md`, but it does
not yet have a repeatable Codex automation path that can turn those small tasks
into reviewable pull requests on a schedule.

## Decision

SEOCHO will add a scheduled Codex maintenance workflow with a GitHub App PR path:

1. add repo-local skill `.agents/skills/daily-maintenance-pr/SKILL.md`
2. add scheduled prompt `.github/codex/prompts/daily-maintenance-pr.md`
3. add workflow `.github/workflows/daily-codex-maintenance.yml`
4. run Codex via `openai/codex-action@v1`
5. create or update the pull request using a GitHub App installation token
6. keep the workflow review-first:
   - no direct push to `main`
   - no auto-merge
   - one small reviewable change per run

## Consequences

Positive:

- recurring small fixes can arrive as consistent PRs instead of ad hoc manual
  sessions
- the repository keeps the prompt and maintenance rules in version control
- GitHub App tokens make PR authorship and permissions explicit

Tradeoffs:

- the workflow depends on repository secrets for both OpenAI access and GitHub
  App authentication
- daily automation must stay narrow or it will create noisy PRs
- maintainers still need to review, merge, or close the PRs; the workflow is
  not a release bot

## Implementation Notes

- required secrets:
  - `OPENAI_API_KEY`
  - `SEOCHO_GITHUB_APP_ID`
  - `SEOCHO_GITHUB_APP_PRIVATE_KEY`
- default schedule is `00:15 UTC` (`09:15 Asia/Seoul`)
- manual trigger remains available through `workflow_dispatch`
