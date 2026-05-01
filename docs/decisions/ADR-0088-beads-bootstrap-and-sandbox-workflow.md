# ADR-0088: Beads Bootstrap And Sandbox Workflow

- Status: Accepted
- Date: 2026-04-19

## Context

The repository moved to `bd 0.60`, where two older operating assumptions no
longer hold:

1. `bd sync` is no longer the supported best-effort workspace recovery step.
2. `bd --no-daemon` is no longer a valid CLI pattern for safe local lint and
   worktree operations.

At the same time, this repository depends on `.beads` being usable in cloned
worktrees, during lint runs, and after branch switches. A broken recovery path
is a release blocker because sprint lint, task tracking, and landing guidance
all depend on beads being readable.

## Decision

Adopt the following repo-wide Beads operating contract:

1. Use `bd bootstrap` as the safe best-effort workspace recovery step.
   - It is a no-op when the workspace database is already healthy.
   - It is the supported repair path for fresh clones and broken local state.
2. Use `bd --sandbox ...` for repo-local issue operations that should avoid
   auto-sync side effects.
   - This replaces prior `bd --no-daemon` guidance in scripts and docs.
3. Keep landing wrappers and lint scripts aligned with the new contract.
   - `scripts/pm/lint-items.sh`
   - `scripts/task-context-trail.sh`
   - `scripts/gt-doctor.sh`
   - `scripts/gt-land.sh`
4. Treat invalid legacy issue rows that fail `bd 0.60` import validation as
   migration artifacts, not canonical repo work items.
5. Use `scripts/pm/bd-recover.sh` as the first-line Beads/Dolt diagnostic.
   - Default mode is read-only.
   - A healthy `bd dolt show` SQL connection overrides a false
     `bd dolt status` `not running` signal.
   - `--fix` is reserved for cases where `bd doctor` or `bd dolt show` fails,
     and only restarts the Dolt SQL server bound to this repo's Beads port.

## Consequences

Positive:

- `.beads` recovery is now aligned with the supported `bd 0.60` workflow.
- lint and worktree-safe reads no longer rely on removed CLI flags.
- landing guidance matches current Beads behavior.
- local Dolt lifecycle failures have a narrow recovery helper before anyone
  reaches for deletion, reinstall, or broad reset commands.

Negative:

- older notes that mention `bd sync` or `BEADS_NO_DAEMON` become historical and
  should not be treated as current operating instructions.
- fresh recovery still depends on a healthy local Dolt server lifecycle, though
  `scripts/pm/bd-recover.sh --fix` can repair the common orphaned-server case.
