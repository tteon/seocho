# AGENTS.md

Execution rules for coding agents in this repository.

## 1. Read First

Before implementing:

1. `README.md`
2. `CLAUDE.md`
3. `docs/WORKFLOW.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`

## 2. Stack Baseline (Must Match)

- OpenAI Agents SDK
- Opik tracing/evaluation
- DozerDB backend
- single-tenant MVP with `workspace_id` propagated
- Owlready2 only in offline ontology governance path

## 3. Work Intake And Tracking

- use `bd ready`, `bd show <id>`, `bd update <id> --status in_progress`
- create standardized work items with:
  - `scripts/pm/new-issue.sh`
  - `scripts/pm/new-task.sh`

Active work items must include collaboration labels:

- `sev-*`, `impact-*`, `urgency-*`, `sprint-*`, `roadmap-*`, `area-*`, `kind-*`

Validate sprint labeling:

```bash
scripts/pm/lint-items.sh --sprint 2026-S03
scripts/pm/sprint-board.sh --sprint 2026-S03
```

## 4. Coding Rules

- use type hints
- keep changes scoped and testable
- no hardcoded secrets
- use centralized config (`extraction/config.py`)
- logging, not print
- avoid broad/hidden side effects

## 5. Runtime Guardrails

- preserve `workspace_id` in runtime API/model changes
- enforce runtime policy checks for new endpoints/actions
- keep heavy ontology reasoning out of hot path

## 6. Required Tests

- add/adjust tests for changed behavior
- run focused pytest suites before commit
- if full suite is not run, state exact gap in handoff

## 7. Landing Rules

1. file follow-up issues for deferred work
2. run relevant quality gates
3. update issue status
4. land:
   - `git pull --rebase`
   - `bd sync` (best effort if workspace issue persists)
   - `git push`
   - `git status` (must show up to date with `origin/main`)

Push target is always `main`.

## 8. Documentation Rules

For architecture or workflow changes:

- update `README.md` + relevant `docs/*`
- record decision in ADR (`docs/decisions/ADR-*.md`)
- append entry to `docs/decisions/DECISION_LOG.md`
