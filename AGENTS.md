# AGENTS.md

Execution rules for coding agents in this repository.

## 1. Read First

Before implementing:

1. `README.md`
2. `CLAUDE.md`
3. `docs/WORKFLOW.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`

If the change touches semantic retrieval, public memory answering, or Graph-RAG
behavior, also read `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.

## 2. Stack Baseline (Must Match)

- OpenAI Agents SDK
- vendor-neutral tracing contract with Opik as the preferred team backend
- DozerDB backend
- single-tenant MVP with `workspace_id` propagated
- Owlready2 only in offline ontology governance path

## 3. Work Intake And Tracking

- use `bd ready`, `bd show <id>`, `bd update <id> --status in_progress`
- create standardized work items with:
  - `scripts/pm/new-issue.sh`
  - `scripts/pm/new-task.sh`
- exception: the scheduled daily Codex maintenance workflow may operate without
  a dedicated `bd` item when the PR itself is the review envelope; in that case
  the PR body must still capture scope, validation, and residual risk

Active work items must include collaboration labels:

- `sev-*`, `impact-*`, `urgency-*`, `sprint-*`, `roadmap-*`, `area-*`, `kind-*`

Validate sprint labeling:

```bash
scripts/pm/lint-items.sh --sprint 2026-S03
scripts/pm/sprint-board.sh --sprint 2026-S03
scripts/pm/lint-agent-docs.sh
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

## 9. GitHub Automation Rules

- basic CI workflow lives in `.github/workflows/ci-basic.yml`
- the local command behind basic CI is `bash scripts/ci/run_basic_ci.sh`
- use repo-local skill `$daily-maintenance-pr` for scheduled or manual Codex
  maintenance PR workflows
- use repo-local skill `$periodic-review-pr` for scheduled or manual Codex
  repository review PR workflows
- scheduled automation prompts live in:
  - `.github/codex/prompts/daily-maintenance-pr.md`
  - `.github/codex/prompts/periodic-review-pr.md`
- scheduled Codex workflows live in:
  - `.github/workflows/daily-codex-maintenance.yml`
  - `.github/workflows/periodic-codex-review.yml`
- comment-based merge workflow lives in
  `.github/workflows/pr-comment-merge.yml`
- scheduled automation must stay small, reviewable, and non-destructive:
  - no direct push to `main`
  - no auto-merge
  - one cohesive change only
  - PR body must include `Feature`, `Why`, `Design`, `Expected Effect`,
    `Impact Results`, `Validation`, and `Risks`
- comment-based merge should stay explicitly maintainer-triggered:
  - merge command is exactly `/go`
  - only users with `write`, `maintain`, or `admin` permission may trigger it
  - workflow uses squash merge
  - workflow expects PR merge state `CLEAN`
