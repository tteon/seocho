# Agent-Driven Development Playbook

## 1. Session Start

1. `bd ready`
2. `bd show <id>`
3. `bd update <id> --status in_progress`
4. define acceptance criteria before coding

## 2. Execution Rules

- Keep changes scoped to one issue.
- Prefer deterministic scripts over ad-hoc commands.
- Record validation evidence for touched modules.
- Open follow-up issues instead of silent scope expansion.

## 3. Validation Gates

- `code gate`: tests/lint/build for changed surface
- `ops gate`: `scripts/ops-check.sh --rig seocho` (when running in town workspace)
- `state gate`: `bd sync` and branch sync status verified

## 4. Landing (Required)

1. `bd close <id>` (or explicit handoff issue)
2. `git pull --rebase`
3. `bd sync`
4. `git push`
5. `git status` must show up to date with origin

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
