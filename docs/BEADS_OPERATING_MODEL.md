# Beads Operating Model

Date: 2026-03-12
Status: Draft

This document defines how `.beads` and `bd` should be used if SEOCHO adopts an explicitly agent-friendly delivery model.

## 1. Why This Exists

Agent-friendly repositories need machine-readable work state.

In SEOCHO, `.beads` should not be treated as an optional personal workflow layer. It should become the repository's operational truth for:

- work intake
- execution state
- handoff state
- follow-up capture
- landing readiness

## 2. Core Policy

No meaningful code change should start without a task or issue ID.

Minimum required flow:

1. `bd ready`
2. `bd show <id>`
3. `bd update <id> --status in_progress`
4. implement with focused scope
5. run relevant gates
6. update or close the work item

This applies to both humans and coding agents.

## 3. What Lives In `.beads`

Tracked planning state:

- `.beads/issues.jsonl`
- `.beads/interactions.jsonl`
- `.beads/config.yaml`

Local runtime artifacts that must not leak into committed state:

- `.beads/beads.db*`
- `.beads/daemon.lock`
- `.beads/daemon.pid`
- `.beads/daemon.log`
- `.beads/bd.sock`
- `.beads/.jsonl.lock`
- `.beads/last-touched`
- `.beads/export-state/*.json`

See also:

- `docs/RUNTIME_FILE_ISOLATION.md`
- `scripts/beads-path-guard.sh`
- `scripts/gt-doctor.sh`

## 4. Required Workflow

### 4.1 Intake

Use:

```bash
bd ready
bd show <id>
bd update <id> --status in_progress
```

Create new work only through:

```bash
scripts/pm/new-issue.sh
scripts/pm/new-task.sh
```

Every active item must include collaboration labels:

- `sev-*`
- `impact-*`
- `urgency-*`
- `sprint-*`
- `roadmap-*`
- `area-*`
- `kind-*`

### 4.2 During Execution

Agents should treat the task ID as a required execution parameter.

Expected behavior:

- do not work on unrelated scope under the same task silently
- create follow-up items for overflow scope
- keep notes and handoff grounded in the active task ID

### 4.3 Before Landing

Required sequence:

1. focused tests
2. `scripts/ops-check.sh --task-id <id> --rig seocho`
3. `scripts/gt-doctor.sh`
4. `git pull --rebase`
5. `bd sync`
6. `git push`
7. `git status`

Wrapper options:

```bash
scripts/gt-land.sh --task-id <id> --pull --push
scripts/land.sh --task-id <id> --fix --pull --push
```

## 5. Context Event Model

The repository already has the beginning of a machine-readable execution trail.

Current event-producing scripts:

- `scripts/ops-check.sh`
- `scripts/gt-land.sh`

Inspection:

```bash
scripts/task-context-trail.sh --task-id <id>
scripts/task-context-trail.sh --task-id <id> --json
```

Context events should be treated as first-class operating evidence, not optional debug logs.

## 6. Agent Rules

If the repository adopts `.beads` fully, agents should follow these rules:

1. refuse to start large implementation work without an active task ID unless the user explicitly asks for exploratory work
2. keep one primary task ID per coherent change set
3. file a follow-up task instead of hiding deferred work in prose only
4. use `.beads` state and labels to understand urgency and scope
5. include the task ID in validation and landing flow whenever supported

## 7. Developer Rules

Humans should follow the same contract as agents.

That means:

- no bypassing intake discipline for “small” changes unless it is a clearly trivial typo
- no hidden work outside tracked scope
- no silent landing without state sync

## 8. Recommended Repository-Level Decisions

To make `.beads` truly authoritative, maintainers should decide the following:

1. Is `.beads` mandatory for all non-trivial work?
2. What counts as “trivial enough” to skip a task?
3. Is every merged change expected to reference a task or issue ID?
4. Should CI fail if branch work is not traceable to a task ID?
5. Should handoff notes be stored only in chat/PR text, or mirrored into `.beads` metadata?

## 9. Recommended Enforcement

### P0

- make `.beads` mandatory for non-trivial work
- document conflict resolution between `.beads` state and free-form notes
- require task ID in landing scripts for all normal merges

### P1

- add CI checks ensuring active items have required labels
- add a docs page describing what “trivial” means
- add conventions for follow-up issue creation from handoffs

### P2

- add task-to-PR or task-to-commit trace conventions
- add richer machine-readable metadata for acceptance criteria and test evidence

## 10. Practical Recommendation

If SEOCHO wants to be genuinely agent-friendly, adopt this rule:

`.beads` is the source of truth for work state, and prose documents explain policy around it.`

That gives agents a stable way to infer:

- what to work on
- how urgent it is
- when work is complete
- how to hand off safely
