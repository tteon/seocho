# Agent-Driven Development Playbook

This playbook defines how work is executed and landed in SEOCHO.
Use it as the default operating contract for both humans and agents.

## 1. Work Intake

### 1.1 Pull and Select Work

- Run `bd ready`.
- Select one issue with clear scope and measurable outcome.
- If issue is ambiguous, rewrite scope before implementation.

### 1.2 Claim Ownership

- Run `bd update <id> --status in_progress`.
- Record owner in issue notes when collaboration is expected.
- Keep one active owner per issue at a time.

### 1.3 Intake Checklist

- [ ] Problem is explicit.
- [ ] Why now is explicit.
- [ ] Acceptance criteria are testable.
- [ ] Impacted path(s) are named.
- [ ] Rollback path is known.

## 2. Planning And Execution

### 2.1 Slice Work

- Split into slices that can each be validated.
- Prefer deterministic commands and scripts over ad-hoc sequences.
- Keep non-essential refactors out of scope unless separately tracked.

### 2.2 Trace Expectations

Each slice should leave:

- one intent link (issue ID),
- one implementation artifact (file diff or generated output),
- one validation artifact (test/check output),
- one decision note when tradeoffs were made.

### 2.3 Execution Checklist

- [ ] Issue state is `in_progress`.
- [ ] Scope drift checked before each slice.
- [ ] Validation command identified before edit.
- [ ] Runtime side-effects reviewed.

## 3. Validation Gates

Validation depth must match risk. At minimum:

- run checks touching changed code path,
- run operational check for rig-level impact,
- verify no secret/path leakage in artifacts.

### 3.1 Gate Categories

- `Code gate`: tests/lint/build relevant to touched files.
- `Ops gate`: `scripts/ops-check.sh --rig seocho`.
- `State gate`: `bd sync` + clean branch status for tracked files.

### 3.2 Gate Checklist

- [ ] Code gate passed.
- [ ] Ops gate passed or failure documented with follow-up issue.
- [ ] State gate passed.

## 4. Landing Procedure

Use this exact order:

1. `bd close <id>` (or create explicit handoff issue if not done).
2. `git pull --rebase`
3. `bd sync`
4. `git push`
5. `git status` (must show up to date with origin)

If push fails, fix and retry until successful.

## 5. Incident Handling

When unexpected state appears:

1. Stop feature edits.
2. Isolate a minimal reproduction.
3. File a bug issue with repro steps and observed behavior.
4. Stabilize environment.
5. Resume feature work only after stability is verified.

### 5.1 Escalation Triggers

Open follow-up issue immediately when:

- root cause is unknown,
- behavior is flaky/non-deterministic,
- fix crosses service boundaries,
- data correctness cannot be proven.

## 6. Handoff Standard

Each handoff must include:

- what changed,
- what remains open,
- exact file paths touched,
- latest check command + result,
- linked issue IDs.

## 7. Context Graph Adoption

Use `docs/CONTEXT_GRAPH_BLUEPRINT.md` as source of truth for context graph rollout.

Minimum per-task trail:

- issue transition (`open -> in_progress -> closed`),
- execution artifacts (files/logs),
- validation result,
- landing result (rebase/sync/push status).

Prefer script-captured events over manual narrative to reduce drift.

## 8. Metrics To Track

- lead time: `in_progress` to `closed`,
- reopen rate,
- hotfix count per week,
- automated validation coverage,
- reproducible run rate.

## 9. Anti-Patterns

- Implementing without issue linkage.
- Closing issue without objective validation.
- Mixing coordination and execution state silently.
- Leaving local-only commits at session end.
- Treating generated/runtime files as source history.
