# ADR-0231: Isolated coding workspaces and linked execution records

Date: 2026-09-13
Status: Accepted

## Context

The maintainer checkout contains active research edits, local agent overlays,
large experiment receipts and several independent worktrees. Treating a clean
Git status as a reason to erase that state would lose work. New contributors and
agents also need an obvious route from a task to its code, tests and decisions.

## Decision

Keep product ownership unchanged. Add read-only workspace inspection and an
explicit task-worktree command based on a local Git reference. Store task
checkouts under the common repository's ignored `.seocho/worktrees`, with local
handoff and base-revision receipts. Never implicitly fetch, install, stash,
switch, delete or merge. Existing tasks are not overwritten.

Keep AGENTS.md canonical. The practical agent workflow links public issue/PR,
local beads, ExecPlan, ADR and immutable run receipts by purpose rather than
copying rules into every file. Tool-required discovery paths stay in place.
Move only reviewed generated clutter, recording original/target paths and hashes;
keep experiment datasets/receipts and unfinished source edits intact.

## Consequences

Tasks gain a clean coding surface even while the original research checkout is
dirty. Worktrees share Git objects but have separate working files. Environments
and services are explicit per task; a worktree is not a service sandbox. Pytest,
Ruff and mypy caches use `.seocho/cache`. A task receipt is local navigation,
not an alternative public tracker or permission to launch paid experiments.

## Validation

Disposable real Git repositories verify dirty-source preservation, paths with
spaces, common-root resolution from nested worktrees, collisions and invalid task
names. Repository/root/docs contracts guard the tracked public surface. Local
cleanup receipts separately prove which actual files moved without claiming
that preserved research work was merged.
