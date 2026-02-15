# ADR-0006: Issue/Task Governance for Sprint + Roadmap Execution

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Work items were not consistently captured with collaboration metadata
(severity, impact, urgency, sprint, roadmap, area), reducing triage quality.

## Decision

Introduce standardized issue/task operating system:

- required label taxonomy for all active items
- dedicated scripts for issue/task creation
- sprint board script for execution visibility
- lint script to enforce required collaboration labels

## Implementation

- `scripts/pm/new-issue.sh`
- `scripts/pm/new-task.sh`
- `scripts/pm/sprint-board.sh`
- `scripts/pm/lint-items.sh`
- `docs/ISSUE_TASK_SYSTEM.md`

## Consequences

Positive:

- triage and sprint planning become consistent
- roadmap linkage is explicit per work item
- collaboration context is machine-queryable via labels

Trade-offs:

- stricter label requirements increase capture overhead
- legacy items may need backfill to satisfy lint checks
