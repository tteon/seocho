# Gastown Coordination

Gastown is the coordination plane for shared-seam work in this repository.

It is intentionally not the planning source of truth. Use `.beads` for issue
state, sprint labels, priority, and roadmap tracking. Use Gastown only to
prevent two agents from writing the same shared seam at the same time.

## Core Rule

- `.beads` answers: what is the work, what is its status, and why does it
  matter
- Gastown answers: who currently owns a shared write scope, from which branch
  or worktree, and for how long

If those responsibilities start to overlap, coordination becomes harder rather
than easier.

## When A Gastown Reservation Is Required

Create a reservation when your change writes to any shared seam in
`.agents/gastown/shared-seams.yaml`.

Current serialized seams:

- `sdk-facade`
- `query-canonical`
- `indexing-canonical`
- `runtime-shell`
- `docs-entry`
- `github-automation`

These seams either change public behavior, touch broad contracts, or generate
high merge-conflict risk.

## Reservation Contract

Each reservation should include:

- `seam_id`
- `owner`
- `bd_id`
- `branch`
- `worktree`
- `write_paths`
- `ttl`
- `status`
- `handoff_note`

Recommended defaults:

- TTL: `24h`
- status: `active`
- one serialized seam per active implementation slice

## Workflow

1. Create or claim the `.beads` item.
2. Create a dedicated git worktree for the slice when practical.
3. Reserve the shared seam in Gastown.
4. Keep writes inside the declared scope.
5. If you need a second serialized seam, split the work or hand off the first.
6. Release or hand off the reservation before merge.

## Merge Policy

For serialized seams, merge order matters more than parallel throughput.

- use one active writer per serialized seam
- keep diffs narrow
- prefer explicit handoff notes over implicit overlap
- use merge-slot style serialization when two PRs want the same seam

## Worktree Guidance

Gastown and worktrees should move together:

- reservation `branch` should match the worktree branch
- reservation `worktree` should be the actual filesystem path
- prefer `BEADS_NO_DAEMON=1` in worktrees to avoid daemon writes crossing
  branches

## Non-Goals

- no second copy of sprint or roadmap state in Gastown
- no attempt to replace `.beads`
- no broad lock on the whole repository
- no permanent ownership; all reservations should expire or hand off

## Shared Seam Registry

The repo-local seam registry lives at:

- `.agents/gastown/shared-seams.yaml`

Update it when:

- a new high-conflict surface appears
- a canonical ownership move changes write paths
- a seam should stop being serialized
