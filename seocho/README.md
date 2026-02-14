# SEOCHO Rig Workspace

This directory is the rig-level operating surface for agent-driven development.

## Purpose

- Keep work moving through explicit agent ownership and handoff.
- Standardize issue lifecycle, quality gates, and landing flow.
- Make every change auditable with logs and issue links.

## Working Areas

- `crew/hardy/`: human-led development workspace.
- `mayor/rig/`: mayor's canonical rig clone for coordination and state.
- `witness/rig/`: health/watch layer for rig execution.
- `refinery/rig/`: merge/readiness execution layer.
- `.beads/`: rig issue tracking context.

## Agent-Driven Flow

1. Pick work from `bd ready`.
2. Claim with `bd update <id> --status in_progress`.
3. Execute in small, verifiable increments.
4. Record operational checks (`scripts/ops-check.sh` at town root).
5. Close with tests + `bd close <id>`.
6. Land through pull/rebase/sync/push discipline.

## Definition Of Done

- The target issue is closed or explicitly handed off.
- Required checks passed and logs captured.
- Follow-up items filed as new issues.
- Changes are committed and pushed.

## Quick Commands

```bash
bd ready
bd show <id>
bd update <id> --status in_progress
bd close <id>

# town-level helpers from repo root
scripts/check-beads-path.sh .
scripts/ops-check.sh --rig seocho --fix
scripts/gt-land.sh --rig seocho
```

## Next Docs

- `AGENTS.md`: execution rules for coding agents.
- `docs/ADD_PLAYBOOK.md`: full operating model.
- `docs/TASK_TEMPLATE.md`: reusable task brief format.
- `docs/CONTEXT_GRAPH_BLUEPRINT.md`: context graph architecture and rollout plan.
