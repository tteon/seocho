# ADR-0077: Gastown Shared-Seam Coordination Plane

## Status

Accepted

## Context

SEOCHO now has multiple concurrent agents and worktrees contributing in
parallel. The current planning model is strong on issue tracking and sprint
state through `.beads`, but that does not by itself prevent overlap on
high-conflict write surfaces such as:

- `seocho/client.py`
- `seocho/query/*`
- `seocho/index/*`
- `runtime/*`
- entry docs and GitHub automation files

Repeated overlap on those surfaces causes merge churn, duplicated work, and
boundary drift.

## Decision

We will use Gastown as a thin coordination plane for shared-seam reservations.

Rules:

1. `.beads` remains the canonical planning and status tracker.
2. Gastown is used only for reservation and handoff of shared write scopes.
3. Shared seams are defined in `.agents/gastown/shared-seams.yaml`.
4. Serialized seams should have one active writer at a time.
5. Reservations should include `bd` id, branch/worktree, write scope, and TTL.
6. Git worktrees remain the preferred execution isolation mechanism.

Default guidance:

- 24-hour TTL
- explicit release or handoff before merge
- `bd --sandbox ...` preferred in worktrees for repo-local issue operations

## Consequences

Positive:

- fewer collisions on canonical ownership boundaries
- clearer handoff between agents
- easier review of changes touching shared seams

Negative:

- one more coordination step for broad or high-risk work
- stale reservations can become friction if TTL and release discipline are weak

## Implementation Notes

- workflow contract: `docs/GASTOWN_COORDINATION.md`
- seam registry: `.agents/gastown/shared-seams.yaml`
- `.beads` remains the only source of sprint, roadmap, and priority state
