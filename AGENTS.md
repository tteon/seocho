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
- `.beads` is the canonical planning and status tracker
- use Gastown only as a coordination plane for shared-seam reservations:
  - claim a reservation after the `bd` item exists
  - include the `bd` id, branch/worktree, write scope, and TTL
  - keep the shared seam registry in `.agents/gastown/shared-seams.yaml`
- exception: the scheduled daily Codex maintenance workflow may operate without
  a dedicated `bd` item when the PR itself is the review envelope; in that case
  the PR body must still capture scope, validation, and residual risk

Shared-seam work should also follow `docs/GASTOWN_COORDINATION.md`.

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
   - release or hand off any active Gastown reservation
   - `git pull --rebase`
   - `bd bootstrap` (best effort; safe no-op when the workspace DB is already healthy)
   - `git push`
   - `git status` (must show up to date with `origin/main`)

When working in a git worktree, prefer `bd --sandbox ...` for repo-local issue
operations that should avoid auto-sync side effects.

Push target is always `main`.

## 8. Documentation Rules

For architecture or workflow changes:

- update `README.md` + relevant `docs/*`
- record decision in ADR (`docs/decisions/ADR-*.md`)
- append entry to `docs/decisions/DECISION_LOG.md`

## 9. Automation Roles

- Codex is the primary PR author for bounded work in this repo:
  - `feature-improvement`: one small developer-facing improvement
  - `refactor`: one maintainability improvement with no intended behavior change
  - `e2e-investigation`: reproduce one concrete issue, add focused regression
    coverage, and apply the smallest viable fix
- Jules is PR-fixer-first:
  - repair failing CI on an existing PR
  - keep scope narrow and directly related to the failing checks
  - do not originate broad feature, refactor, or architecture work unless
    explicitly asked
- Human maintainers remain the merge gate:
  - review scope and validation
  - move draft PRs to review-ready state
  - trigger merge explicitly

## 10. GitHub Automation Rules

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
  - choose exactly one Codex lane: `feature-improvement`, `refactor`, or
    `e2e-investigation`
  - PR body must include `Feature`, `Why`, `Design`, `Expected Effect`,
    `Impact Results`, `Validation`, and `Risks`
- Jules should treat existing PRs as the primary unit of work:
  - fix only failing GitHub Actions checks and closely related narrow issues
  - preserve the current PR intent
  - avoid semantic retrieval, ontology policy, routing policy, tracing contract,
    or multi-agent behavior changes unless the PR already targets that exact
    area
- comment-based merge should stay explicitly maintainer-triggered:
  - merge command is exactly `/go`
  - only users with `write`, `maintain`, or `admin` permission may trigger it
  - workflow uses squash merge
  - workflow expects PR merge state `CLEAN`
