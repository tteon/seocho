# SEOCHO Rig Workspace

This directory is the rig-level operating surface for agent-driven development.
It is optimized for repeatable execution, clear ownership, and auditable delivery.

## What This Workspace Is For

- Ship work through explicit issue ownership (`bd`).
- Make every task reproducible with command and artifact traces.
- Keep coordination (`mayor`) and execution (`crew`) separate.
- Land changes safely with consistent sync/rebase/push discipline.

## Directory Map

- `crew/hardy/`: human-led execution workspace.
- `mayor/rig/`: canonical coordination state and policy anchor.
- `witness/rig/`: health/watch layer.
- `refinery/rig/`: readiness and merge execution layer.
- `docs/`: operating model, templates, architecture notes.
- `.beads/`: issue state store for `bd`.

## Quick Start (New Session)

Run from town root (`/home/hadry/gt`):

```bash
scripts/check-beads-path.sh .
bd ready
bd show <id>
bd update <id> --status in_progress
```

Then execute work in small slices and validate:

```bash
# run focused checks for changed area first
scripts/ops-check.sh --rig seocho
```

Close and land:

```bash
bd close <id>
git pull --rebase
bd sync
git push
git status
```

Expected final status:

- issue is `closed` (or explicit handoff issue exists),
- branch is up to date with `origin/master`,
- validation artifacts are traceable.

## Standard Delivery Loop

1. Intake
2. Clarify acceptance criteria
3. Implement in verifiable slices
4. Validate changed surface
5. Sync issue state
6. Rebase, push, and verify branch state

## Role Boundaries

- `crew`: implements and validates task-level changes.
- `mayor`: holds coordination policy and canonical process decisions.
- `refinery`: applies readiness/merge discipline.
- `witness`: monitors rig health and execution anomalies.

When boundaries blur, create a follow-up issue rather than extending scope silently.

## Definition Of Done

- Target issue resolved and status updated.
- Required checks passed for the touched surface.
- Follow-up risks captured as new `bd` issues.
- Artifacts/logs available for replay.
- Commit is pushed to remote.

## Common Failure Cases

- Missing issue link to code change.
- Validation run omitted or not recorded.
- Local-only commit not pushed.
- Runtime/generated files treated as source of truth.

Use `docs/ADD_PLAYBOOK.md` incident flow when any of the above appears.

## Document Map

- `AGENTS.md`: execution constraints and handoff minimum.
- `docs/ADD_PLAYBOOK.md`: full operating model and checklists.
- `docs/TASK_TEMPLATE.md`: task brief template for consistent intake.
- `docs/CONTEXT_GRAPH_BLUEPRINT.md`: context graph model and rollout plan.
