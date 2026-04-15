---
name: "runtime-migration-slice"
description: "Use for staged SEOCHO extraction-to-runtime migration work. Trigger when a task moves canonical deployment-shell ownership from extraction/ to runtime/, updates compatibility aliases, or normalizes active docs/tests/CI around runtime/* paths without broad behavior redesign."
---

# Runtime Migration Slice

Use this skill for bounded `extraction/ -> runtime/` migration slices in
SEOCHO.

The goal is not a big-bang rename. The goal is one reviewable slice that makes
`runtime/` more canonical while preserving compatibility.

Read `references/runtime_shell_validation.md` before editing.

## Required Read Order

1. `AGENTS.md`
2. `README.md`
3. `CLAUDE.md`
4. `docs/WORKFLOW.md`
5. `docs/RUNTIME_PACKAGE_MIGRATION.md`
6. `docs/decisions/DECISION_LOG.md`

If the slice touches semantic retrieval or semantic runtime flow, also read
`docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.

## Allowed Scope

Pick one cohesive slice from:

- move one runtime-shell module under `runtime/`
- convert one `extraction/*` runtime module into a compatibility alias
- normalize repo-owned tests/docs/CI to canonical `runtime/*` paths
- add a narrow migration guardrail such as a fast contract check or hook

## Avoid

Do not use this skill for:

- broad runtime redesign
- semantic retrieval policy changes
- runtime ingest performance work
- historical ADR or archive rewrites
- removing compatibility aliases without an explicit migration slice

## Canonical Pattern

1. `runtime/*` becomes the canonical owner.
2. `extraction/*` stays as a compatibility alias or thin caller.
3. Repo-owned tests and active docs prefer `runtime/*`.
4. Current workflow docs, ADR, and `DECISION_LOG` are updated.
5. `.beads` reflects landed slice state.

## Validation

Run the smallest relevant validation set:

- focused `py_compile`
- focused `uv run pytest ...`
- `bash scripts/ci/check-runtime-shell-contract.sh` when runtime shell paths are touched
- `bash scripts/pm/lint-agent-docs.sh`
- `git diff --check`

Run `bash scripts/ci/run_basic_ci.sh` when the touched behavior falls inside the
current basic CI surface.

## Final Response

Report:

- canonical owner moved or normalized
- compatibility surface kept
- validations run
- residual migration risk
