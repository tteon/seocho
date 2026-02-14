# Agent-Driven Development Playbook

## 1. Work Intake

- Pull ready work via `bd ready`.
- Reject ambiguous tasks; rewrite as testable outcomes.
- Add acceptance criteria before coding if missing.

## 2. Ownership Model

- One owner per issue at a time.
- Multi-agent collaboration is allowed, but ownership remains explicit.
- Use comments or notes for decision trace, not private memory.

## 3. Execution Model

- Plan in slices that can each be validated.
- Prefer deterministic commands and scripted checks.
- Keep operational checks in logs for replayability.

## 4. Validation Gates

- Unit/integration checks relevant to changed surface.
- Operational health check for rig impact.
- No hidden side effects in runtime or generated artifacts.

## 5. Landing

- Sync issues (`bd sync`) before final git operations.
- Rebase on remote head.
- Push and confirm branch status.

## 6. Incident Handling

When unexpected state appears:

- stop feature work,
- isolate root cause,
- file bug issue with reproduction,
- resume only after state is stable.

## 7. Metrics To Track

- Lead time from `in_progress` to `closed`.
- Reopen rate.
- Number of hotfixes per week.
- % of changes with automated validation.

## 8. Context Graph Adoption

- Use `docs/CONTEXT_GRAPH_BLUEPRINT.md` as the source of truth for schema and rollout.
- Every task should leave a machine-verifiable trail:
  - issue transition,
  - execution artifacts,
  - validation result,
  - landing status.
- Prefer script-level event capture over manual notes to reduce drift.
