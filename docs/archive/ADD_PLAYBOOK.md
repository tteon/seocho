# Agent-Driven Development Playbook

Archived: use `docs/WORKFLOW.md` and `docs/BEADS_OPERATING_MODEL.md` as the active source of truth.

## 1. Session Start

1. `bd ready`
2. `bd show <id>`
3. `bd update <id> --status in_progress`
4. define acceptance criteria before coding
5. (once per clone) `scripts/pm/install-git-hooks.sh`

## 2. Execution Rules

- Keep changes scoped to one issue.
- Prefer deterministic scripts over ad-hoc commands.
- Record validation evidence for touched modules.
- Open follow-up issues instead of silent scope expansion.

## 3. Validation Gates

- `code gate`: tests/lint/build for changed surface
- `ops gate`: `scripts/ops-check.sh --task-id <id> --rig seocho`
- `doctor gate` (ops hygiene): `scripts/gt-doctor.sh`
  - issue source default is `bd` (`--issues-source auto`), with JSONL fallback (`--issues-source file` to force)
  - safe wisp normalization can be applied with `scripts/gt-doctor.sh --fix`
- single beads path guard: `scripts/beads-path-guard.sh` (`--auto-clean` for redirect/local artifact conflict recovery)
- runtime file isolation policy: `docs/RUNTIME_FILE_ISOLATION.md` (`gt-doctor` enforces tracked runtime-file violations)
- embedded clone policy: `docs/EMBEDDED_GIT_CLONE_POLICY.md` (`gt-doctor` enforces ignored-path/no-submodule rule)
- `state gate`: `bd sync` and branch sync status verified
- context events are recorded to `logs/context/events.jsonl` using schema `docs/schemas/context-event.schema.json`

## 4. Landing (Required)

1. `bd close <id>` (or explicit handoff issue)
2. `git pull --rebase`
3. `bd sync`
4. `git push`
5. `git status` must show up to date with origin

Optional wrapper:

- `scripts/gt-land.sh --task-id <id> --pull --push`
- one-command pipeline: `scripts/land.sh --task-id <id> --fix --pull --push`

## 5. Incident Handling

When unexpected state appears:

1. stop feature edits
2. isolate reproducible case
3. file bug issue with reproduction
4. stabilize environment
5. resume feature work

## 6. Handoff Minimum

- what changed
- open items
- file paths touched
- latest check command and result
- linked issue IDs

## 7. Context Graph Debugging

Use this workflow when context edges are missing/incorrect (for example, missing gate chain, run disconnected from landing, or mismatched `run_id`).

### 7.1 Inspect trail for one task

```bash
scripts/task-context-trail.sh --task-id <id>
scripts/task-context-trail.sh --task-id <id> --json
```

Expected minimum lifecycle for an execution run:

- `run_started`
- one or more `gate_result`
- (`landing_result` for landing flow)
- `run_finished`

### 7.2 Common failure modes

- Missing `run_started`: later events cannot be connected reliably.
- Missing `run_finished`: run appears dangling in graph materialization.
- `gate_result` with wrong `run_id`: checks are attached to the wrong run node.
- No `landing_result` from `gt-land`: landing state cannot be audited.
- Invalid payload shape: event exists but cannot be interpreted by consumers.

### 7.3 Recovery workflow

1. Inspect task trail and identify first broken/missing event.
2. Validate raw JSONL against `docs/schemas/context-event.schema.json`.
3. Re-run `scripts/ops-check.sh` or `scripts/gt-land.sh` with explicit `--run-id` to reconstruct a coherent chain.
4. Re-check with `scripts/task-context-trail.sh --task-id <id>` and confirm lifecycle is contiguous.
