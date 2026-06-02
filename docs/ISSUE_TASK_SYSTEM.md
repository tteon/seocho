# Issue & Task System

SEOCHO uses public GitHub issues and pull requests as the canonical public
planning and review trail. Local agent trackers can be useful for maintainers,
but their state directories are private workspace data and must not be tracked
in this repository.

## Work Item Types

- `issue`: defect, regression, outage, user-facing gap, or open decision
- `task`: implementation unit, refactor, integration, documentation, or CI work

Every active public item should carry enough metadata for a reviewer to
understand priority without opening private tooling:

- severity: `critical`, `high`, `medium`, or `low`
- impact: `critical`, `high`, `medium`, or `low`
- urgency: `now`, `this_sprint`, `next_sprint`, or `later`
- area: `sdk`, `runtime`, `ontology`, `docs`, `infra`, `examples`, or another
  explicit ownership area
- kind: `bug`, `task`, `refactor`, `docs`, `ci`, or `feature`

## Pull Request Contract

Each PR should state:

- Feature: what changed
- Why: why the change belongs in this public middleware repo
- Design: the important implementation choice
- Expected Effect: what improves for users or maintainers
- Validation: exact commands run
- Risks: known gaps or follow-up work

Scheduled automation uses the same review envelope and must remain draft-only
until a maintainer explicitly promotes it.

## Local Tooling Boundary

Do not commit local workflow directories such as:

- `.agents/`
- `.beads/`
- `.claude/`
- `.githooks/`
- `.jules/`
- `.serena/`

If a local tool produces a durable decision, copy the durable decision into a
public issue, PR body, ADR, or `docs/*` contract instead of committing the tool
state itself.

## Sprint Cadence

1. Select public issues or tasks for the sprint.
2. Ensure each selected item has severity, impact, urgency, area, and kind.
3. Execute through PRs with explicit validation.
4. Close or re-scope unfinished work publicly; do not hide follow-up state in
   local agent databases.

## Roadmap Linking Rule

Each item should map to one primary roadmap label or milestone. If work spans
multiple tracks, split it into separate items so reviews stay bounded.
